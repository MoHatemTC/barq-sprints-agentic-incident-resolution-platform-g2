import os
import time
import logging
from functools import wraps
from typing import Any, Callable, Dict, Optional

# In Langfuse v4+, observe and get_client are imported directly from langfuse
try:
    from langfuse import observe, get_client
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    observe = None
    get_client = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret sanitization
# ---------------------------------------------------------------------------

SENSITIVE_KEYS = {
    "password", "token", "secret", "auth", "authorization",
    "key", "credential", "pii", "api_key", "access_token",
    "refresh_token", "private_key", "ssn", "credit_card",
}


def sanitize_payload(payload: Any) -> Any:
    """Remove potential secrets from payload before tracing."""
    if not isinstance(payload, dict):
        return payload
    sanitized = payload.copy()
    for k, v in sanitized.items():
        if any(sec in k.lower() for sec in SENSITIVE_KEYS):
            sanitized[k] = "***REDACTED***"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_payload(v)
        elif isinstance(v, list):
            sanitized[k] = [
                sanitize_payload(item) if isinstance(item, dict) else item
                for item in v
            ]
    return sanitized


# ---------------------------------------------------------------------------
# Root trace decorator — keys trace to execution_id & incident_number
# ---------------------------------------------------------------------------

def trace_execution(name: str):
    """
    Root trace decorator for worker/pipeline execution.
    Creates a single Langfuse trace per execution keyed to
    the execution_id and incident_number.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not LANGFUSE_AVAILABLE or observe is None:
                return func(*args, **kwargs)

            try:
                from langfuse.decorators import langfuse_context
            except ImportError:
                return func(*args, **kwargs)

            # Extract identifiers for keying
            exec_id = kwargs.get("execution_id") or (
                args[1] if len(args) > 1 else None
            )
            inc_num = kwargs.get("incident_number") or (
                args[2] if len(args) > 2 else None
            )

            # Build the observed function dynamically
            @observe(name=name)
            @wraps(func)
            def _traced(*a, **kw):
                return func(*a, **kw)

            # Sanitize inputs
            clean_kwargs = sanitize_payload(kwargs)

            start = time.perf_counter()
            try:
                result = _traced(*args, **clean_kwargs)

                # Update trace with execution identifiers
                try:
                    update_kwargs = {}
                    if exec_id:
                        update_kwargs["session_id"] = str(exec_id)
                    if inc_num:
                        update_kwargs["user_id"] = str(inc_num)
                    if update_kwargs:
                        langfuse_context.update_current_trace(**update_kwargs)
                except Exception as ctx_err:
                    logger.debug(f"Could not update trace context: {ctx_err}")

                return result

            except Exception as e:
                # Record the error in the current observation
                try:
                    langfuse_context.update_current_observation(
                        level="ERROR",
                        status_message=str(e),
                    )
                except Exception:
                    pass
                raise
            finally:
                elapsed = time.perf_counter() - start
                logger.info(
                    f"[tracing] {name} completed in {elapsed:.3f}s"
                )
                # Flush — never crash on flush failure
                try:
                    client = get_client()
                    if client:
                        client.flush()
                except Exception as flush_err:
                    logger.warning(f"Langfuse flush error: {flush_err}")

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Node-level trace decorator — captures latency, outcome, and errors
# ---------------------------------------------------------------------------

def trace_node(name: str, observation_type: str = "span"):
    """
    Decorator for LangGraph nodes to emit child observation spans.
    Captures latency, outcome, and errors per node.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not LANGFUSE_AVAILABLE or observe is None:
                return func(*args, **kwargs)

            @observe(name=name, as_type=observation_type)
            @wraps(func)
            def _traced(*a, **kw):
                return func(*a, **kw)

            # Sanitize dict arguments
            clean_args = [
                sanitize_payload(a) if isinstance(a, dict) else a
                for a in args
            ]
            clean_kwargs = sanitize_payload(kwargs)

            start = time.perf_counter()
            try:
                result = _traced(*clean_args, **clean_kwargs)

                # Record outcome in the observation
                try:
                    from langfuse.decorators import langfuse_context
                    langfuse_context.update_current_observation(
                        level="DEFAULT",
                        status_message="success",
                    )
                except Exception:
                    pass

                return result

            except Exception as e:
                # Record error in observation — never let tracing crash execution
                try:
                    from langfuse.decorators import langfuse_context
                    langfuse_context.update_current_observation(
                        level="ERROR",
                        status_message=f"{type(e).__name__}: {e}",
                    )
                except Exception:
                    pass
                raise
            finally:
                elapsed = time.perf_counter() - start
                logger.debug(f"[tracing] node={name} latency={elapsed:.3f}s")

        return wrapper
    return decorator

