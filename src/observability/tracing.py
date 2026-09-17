import os
import logging
from functools import wraps
from typing import Any, Callable, Dict, Optional

# Import Langfuse AFTER loading environment variables
try:
    from langfuse import Langfuse
    from langfuse.decorators import langfuse_context, observe
except ImportError:
    Langfuse = None
    langfuse_context = None
    def observe(*args, **kwargs):
        def decorator(func):
            return func
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return decorator

logger = logging.getLogger(__name__)

def get_langfuse_client():
    if Langfuse is not None:
        try:
            return Langfuse()
        except Exception as e:
            logger.error(f"Failed to initialize Langfuse client: {e}")
    return None

def sanitize_payload(payload: Any) -> Any:
    """Remove potential secrets from payload before tracing."""
    if not isinstance(payload, dict):
        return payload
    sanitized = payload.copy()
    sensitive_keys = {"password", "token", "secret", "auth", "authorization", "key", "credential", "pii"}
    for k, v in sanitized.items():
        if any(sec in k.lower() for sec in sensitive_keys):
            sanitized[k] = "***REDACTED***"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_payload(v)
        elif isinstance(v, list):
            sanitized[k] = [sanitize_payload(item) if isinstance(item, dict) else item for item in v]
    return sanitized

def trace_execution(name: str):
    """
    Decorator to wrap a root function execution (e.g., worker pickup) in a Langfuse trace.
    Captures latency, outcome, and errors without crashing on failure.
    Requires execution_id and incident_number in kwargs.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not Langfuse:
                return func(*args, **kwargs)
                
            exec_id = kwargs.get("execution_id", "unknown_execution")
            incident_no = kwargs.get("incident_number", "unknown_incident")
            
            client = get_langfuse_client()
            trace = None
            if client:
                try:
                    trace = client.trace(
                        name=name,
                        id=exec_id,
                        tags=[f"incident:{incident_no}"],
                        session_id=exec_id,
                        input=sanitize_payload(kwargs)
                    )
                except Exception as e:
                    logger.error(f"Tracing start failed: {e}")

            try:
                result = func(*args, **kwargs)
                if trace:
                    try:
                        trace.update(output=sanitize_payload(result) if isinstance(result, dict) else str(result))
                    except Exception as e:
                        logger.error(f"Tracing update failed: {e}")
                return result
            except Exception as func_err:
                if trace:
                    try:
                        trace.update(level="ERROR", status_message=str(func_err))
                    except Exception as e:
                        logger.error(f"Tracing error update failed: {e}")
                raise func_err
            finally:
                if client:
                    try:
                        client.flush()
                    except Exception as e:
                        logger.error(f"Langfuse flush failed: {e}")
        return wrapper
    return decorator

def trace_node(name: str, observation_type: str = "span"):
    """
    Decorator for LangGraph nodes. Uses Langfuse native observe.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if observe is not None and getattr(observe, '__module__', None) == 'langfuse.decorators':
                try:
                    @observe(name=name, as_type=observation_type, capture_input=False, capture_output=False)
                    def inner_wrapper(*inner_args, **inner_kwargs):
                        if langfuse_context:
                            sanitized_args = [sanitize_payload(a) if isinstance(a, dict) else a for a in inner_args]
                            sanitized_kwargs = sanitize_payload(inner_kwargs)
                            langfuse_context.update_current_observation(input={"args": sanitized_args, "kwargs": sanitized_kwargs})
                        
                        res = func(*inner_args, **inner_kwargs)
                        
                        if langfuse_context:
                            langfuse_context.update_current_observation(output=sanitize_payload(res) if isinstance(res, dict) else str(res))
                        return res
                    return inner_wrapper(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Langfuse native observe failed for node {name}: {e}. Falling back.")
                    return func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        return wrapper
    return decorator
