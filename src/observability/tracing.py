import os
import time
import logging
import contextvars
from functools import wraps
from typing import Any, Callable, Dict, Optional

# Ensure .env is loaded before Langfuse reads LANGFUSE_PUBLIC_KEY etc.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
# Context variable to propagate the root trace_id across nodes
# ---------------------------------------------------------------------------
_current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_trace_id", default=None
)

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
# Root trace decorator — creates ONE Langfuse trace per execution
# ---------------------------------------------------------------------------

def trace_execution(name: str):
    """
    Root trace decorator for worker/pipeline execution.
    Creates a single Langfuse trace per execution keyed to
    the execution_id and incident_number, and propagates the
    trace_id via a context variable so child nodes nest under it.

    Uses Langfuse v4 SDK:
      - client.create_trace_id() to generate a deterministic trace ID
      - client.start_as_current_observation() as a context manager for
        the root span (which implicitly creates the trace)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not LANGFUSE_AVAILABLE or get_client is None:
                return func(*args, **kwargs)

            # Extract identifiers for keying
            exec_id = kwargs.get("execution_id") or (
                args[1] if len(args) > 1 else None
            )
            inc_num = kwargs.get("incident_number") or (
                args[2] if len(args) > 2 else None
            )

            client = get_client()

            # Generate a deterministic trace_id from execution_id
            trace_id = client.create_trace_id(
                seed=str(exec_id) if exec_id else None
            )

            token = _current_trace_id.set(trace_id)
            start = time.perf_counter()

            try:
                with client.start_as_current_observation(
                    name=name,
                    as_type="span",
                    trace_context={"trace_id": trace_id},
                    input=sanitize_payload(kwargs),
                    metadata={
                        "session_id": str(exec_id) if exec_id else None,
                        "user_id": str(inc_num) if inc_num else None,
                    },
                ) as root_span:
                    try:
                        result = func(*args, **kwargs)

                        # Record success on the root span
                        try:
                            root_span.update(
                                output=sanitize_payload(result) if isinstance(result, dict) else str(result),
                                level="DEFAULT",
                                status_message="success",
                            )
                        except Exception:
                            pass

                        return result

                    except Exception as e:
                        # Record the error on the root span
                        try:
                            root_span.update(
                                level="ERROR",
                                status_message=f"{type(e).__name__}: {e}",
                            )
                        except Exception:
                            pass
                        raise

            except Exception as e:
                # If it's a Langfuse SDK error (not a user func error), fall back
                # to running the function without tracing. Re-raise user errors.
                if isinstance(e, (AttributeError, TypeError)) and "start_as_current_observation" in str(e):
                    logger.debug(f"Langfuse tracing unavailable: {e}")
                    return func(*args, **kwargs)
                raise

            finally:
                elapsed = time.perf_counter() - start
                logger.info(
                    f"[tracing] {name} completed in {elapsed:.3f}s"
                )
                # Reset context
                _current_trace_id.reset(token)
                # Flush — never crash on flush failure
                try:
                    if client:
                        client.flush()
                except Exception as flush_err:
                    logger.warning(f"Langfuse flush error: {flush_err}")

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Node-level trace decorator — creates child spans under the root trace
# ---------------------------------------------------------------------------

def trace_node(name: str, observation_type: str = "span"):
    """
    Decorator for LangGraph nodes to emit child observation spans.
    Captures latency, outcome, and errors per node.
    If a root trace_id exists in context (set by trace_execution),
    the span is nested under it — ensuring all nodes in one execution
    share a single correlated trace.

    Uses Langfuse v4 SDK:
      - client.start_observation() to create a child span
      - span.update() to record output/level/status before ending
      - span.end() to close the span (takes only optional end_time)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not LANGFUSE_AVAILABLE or get_client is None:
                return func(*args, **kwargs)

            client = get_client()
            parent_trace_id = _current_trace_id.get()

            # Sanitize dict arguments
            clean_args = [
                sanitize_payload(a) if isinstance(a, dict) else a
                for a in args
            ]
            clean_kwargs = sanitize_payload(kwargs)

            # Build trace_context so this span nests under the root trace
            trace_context = {"trace_id": parent_trace_id} if parent_trace_id else None

            # Create span via the v4 API
            span = None
            try:
                span = client.start_observation(
                    name=name,
                    as_type="span",
                    trace_context=trace_context,
                    input={"args": [str(a)[:500] for a in clean_args], "kwargs": clean_kwargs},
                )
            except Exception:
                pass

            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)

                # Record outcome in the span: update() then end()
                if span:
                    try:
                        span.update(
                            output=sanitize_payload(result) if isinstance(result, dict) else str(result),
                            level="DEFAULT",
                            status_message="success",
                        )
                        span.end()
                    except Exception:
                        pass

                return result

            except Exception as e:
                # Record error — never let tracing crash execution
                if span:
                    try:
                        span.update(
                            level="ERROR",
                            status_message=f"{type(e).__name__}: {e}",
                        )
                        span.end()
                    except Exception:
                        pass
                raise
            finally:
                elapsed = time.perf_counter() - start
                logger.debug(f"[tracing] node={name} latency={elapsed:.3f}s")

        return wrapper
    return decorator

try:
    from langfuse.langchain import CallbackHandler
except ImportError:
    CallbackHandler = None
def get_llm_callback():
    """
    Returns a LangChain CallbackHandler tied to the current trace.
    Pass this to llm.invoke(..., config={"callbacks": [get_llm_callback()]})
    """
    if not LANGFUSE_AVAILABLE or CallbackHandler is None:
        return []
        
    parent_trace_id = _current_trace_id.get()
    
    # Initialize the handler and link it to the existing trace tree
    trace_context = {"trace_id": parent_trace_id} if parent_trace_id else None
    handler = CallbackHandler(trace_context=trace_context)
    
    return [handler]