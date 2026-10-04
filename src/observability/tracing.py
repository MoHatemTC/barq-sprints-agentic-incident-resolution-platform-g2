import time
import logging
import contextvars
from functools import wraps
from typing import Any, Callable, Optional

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

# interrupt() pauses a graph by raising GraphInterrupt (a GraphBubbleUp)
from langgraph.errors import GraphBubbleUp


def _extract_execution_ids(args, kwargs) -> tuple[Any, Any]:
    """Resolve execution_id / incident_number from a decorated call.

    Bound methods pass ``self`` first, then the incident dict. Taking
    ``args[1]`` blindly used to stamp the whole payload as the execution id,
    so worker.pickup cost never matched the per-node accumulator.
    """
    exec_id = kwargs.get("execution_id")
    inc_num = kwargs.get("incident_number")
    positional = list(args)
    if positional and not isinstance(positional[0], (dict, str)):
        positional = positional[1:]
    if exec_id is None:
        for arg in positional:
            if isinstance(arg, str) and arg:
                exec_id = arg
                break
            if isinstance(arg, dict) and arg.get("execution_id"):
                exec_id = arg.get("execution_id")
                break
    if inc_num is None:
        for arg in positional:
            if isinstance(arg, dict):
                inc_num = arg.get("number") or arg.get("sys_id")
                break
        if inc_num is None:
            strings = [a for a in positional if isinstance(a, str)]
            if exec_id and exec_id in strings:
                strings = [s for s in strings if s != exec_id]
            if strings:
                inc_num = strings[0]
    return exec_id, inc_num


def _attach_root_execution_cost(root_span, client, exec_id) -> None:
    """Write the run's summed LLM usage onto worker.pickup (and the trace).

    Child generations already carry their own Langfuse usage. The parent span
    does not roll those up unless we set usage_details / cost_details here.
    """
    if root_span is None or not exec_id:
        return
    try:
        from src.agent.cost_tracking import snapshot_cost
        snap = snapshot_cost(str(exec_id))
        if not snap:
            return
        usage_details = {
            "input": snap["total_tokens_in"],
            "output": snap["total_tokens_out"],
            "total": snap["total_tokens_in"] + snap["total_tokens_out"],
        }
        cost_details = {
            "input": round(snap["input_cost_usd"], 8),
            "output": round(snap["output_cost_usd"], 8),
            "total": round(snap["total_cost_usd"], 8),
        }
        metadata = {
            "total_tokens_in": snap["total_tokens_in"],
            "total_tokens_out": snap["total_tokens_out"],
            "estimated_cost_usd": round(snap["total_cost_usd"], 8),
        }
        # Stamp the parent span (worker.pickup) AND the trace header. Langfuse
        # shows generation-level usage automatically; the parent does not roll
        # up unless usage_details / cost_details are set here.
        updated = False
        for payload in (
            {"usage_details": usage_details, "cost_details": cost_details, "metadata": metadata},
            {"metadata": metadata},
        ):
            try:
                root_span.update(**payload)
                updated = True
                break
            except TypeError:
                continue
            except Exception:
                logger.debug("Langfuse root span cost update failed", exc_info=True)
                break
        if not updated:
            logger.debug("Langfuse root span rejected usage payload")
        for updater in (
            getattr(root_span, "update_trace", None),
            getattr(client, "update_current_trace", None) if client is not None else None,
        ):
            if not callable(updater):
                continue
            try:
                updater(
                    usage_details=usage_details,
                    cost_details=cost_details,
                    metadata=metadata,
                )
                break
            except TypeError:
                try:
                    updater(metadata=metadata)
                    break
                except Exception:
                    continue
            except Exception:
                continue
        logger.info(
            "Langfuse root cost attached for %s: in=%d out=%d usd=%.6f",
            exec_id, snap["total_tokens_in"], snap["total_tokens_out"], snap["total_cost_usd"],
        )
    except Exception:
        logger.debug("Langfuse root cost attach failed", exc_info=True)


def _is_graph_pause(error: BaseException) -> bool:
    """A LangGraph pause for human review is control flow, not a failure."""
    return isinstance(error, GraphBubbleUp)

# ---------------------------------------------------------------------------
# Context variables to propagate the root trace_id and current span_id
# across nodes and LLM calls, enabling proper parent-child span hierarchy:
#   trace → node span → LLM generation
# ---------------------------------------------------------------------------
_current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_trace_id", default=None
)
_current_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_span_id", default=None
)
_current_root_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_root_span_id", default=None
)
_current_execution_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_execution_id", default=None
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
            exec_id, inc_num = _extract_execution_ids(args, kwargs)

            # Always set execution_id context so cost tracking works even
            # when Langfuse is unavailable.
            exec_id_token = _current_execution_id.set(str(exec_id) if exec_id else None)

            if not LANGFUSE_AVAILABLE or get_client is None:
                try:
                    return func(*args, **kwargs)
                finally:
                    _current_execution_id.reset(exec_id_token)

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
                    root_span_token = None
                    if hasattr(root_span, "id"):
                        root_span_token = _current_root_span_id.set(root_span.id)
                    try:
                        result = func(*args, **kwargs)

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
                            if _is_graph_pause(e):
                                root_span.update(
                                    level="DEFAULT",
                                    status_message="paused for human approval",
                                )
                            else:
                                root_span.update(
                                    level="ERROR",
                                    status_message=f"{type(e).__name__}: {e}",
                                )
                        except Exception:
                            pass
                        raise
                    finally:
                        # Always stamp summed usage on worker.pickup, including pauses.
                        _attach_root_execution_cost(root_span, client, exec_id)
                        if root_span_token is not None:
                            _current_root_span_id.reset(root_span_token)

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
                _current_execution_id.reset(exec_id_token)
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

    Structured input/output: instead of dumping the full state dict,
    each span receives a curated summary via build_node_input /
    build_node_output so Langfuse remains readable and scannable.

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
            parent_root_span_id = _current_root_span_id.get()

            # Extract the state dict (first positional arg for node functions)
            state_arg = args[0] if args else kwargs.get("state", {})
            structured_input = {}
            if isinstance(state_arg, dict):
                try:
                    structured_input = build_node_input(name, state_arg)
                except Exception:
                    structured_input = {"args": str(state_arg)[:300]}

            # Build trace_context so this span nests under the root span
            trace_context = None
            if parent_trace_id:
                trace_context = {"trace_id": parent_trace_id}
                if parent_root_span_id:
                    trace_context["parent_span_id"] = parent_root_span_id

            # Prefix with "node." for clear identification in Langfuse tree
            span_name = f"node.{name}" if not name.startswith("node.") else name

            # Create span via the v4 API
            span = None
            span_token = None
            try:
                span = client.start_observation(
                    name=span_name,
                    as_type="span",
                    trace_context=trace_context,
                    input=sanitize_payload(structured_input),
                )
                # Propagate this span's ID so get_llm_callback() can nest
                # LLM calls as children of this node span (fixes BUG-16).
                if span and hasattr(span, "id"):
                    span_token = _current_span_id.set(span.id)
            except Exception:
                pass

            # Snapshot token counts before this node runs
            acc = None
            tin_before = 0
            tout_before = 0
            execution_id = _current_execution_id.get()
            if not execution_id and isinstance(state_arg, dict):
                execution_id = state_arg.get("execution_id")
                if execution_id:
                    _current_execution_id.set(str(execution_id))

            if execution_id:
                try:
                    from src.agent.cost_tracking import get_accumulator
                    acc = get_accumulator(str(execution_id))
                    tin_before = acc.tokens_in
                    tout_before = acc.tokens_out
                except Exception:
                    pass

            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)

                # Build structured output for this node
                structured_output = {}
                if isinstance(result, dict):
                    try:
                        structured_output = build_node_output(name, result)
                    except Exception:
                        structured_output = sanitize_payload(result)

                # Compute tokens and cost consumed specifically by this node
                node_tin = (acc.tokens_in - tin_before) if acc else 0
                node_tout = (acc.tokens_out - tout_before) if acc else 0
                cost_kwargs = {}
                if node_tin > 0 or node_tout > 0:
                    try:
                        from src.agent.cost_tracking import calculate_cost
                        in_c, out_c, tot_c = calculate_cost(node_tin, node_tout)
                        cost_kwargs = {
                            "usage_details": {
                                "input": node_tin,
                                "output": node_tout,
                                "total": node_tin + node_tout,
                            },
                            "cost_details": {
                                "input": round(in_c, 8),
                                "output": round(out_c, 8),
                                "total": round(tot_c, 8),
                            },
                            "metadata": {
                                "node_tokens_in": node_tin,
                                "node_tokens_out": node_tout,
                                "node_cost_usd": round(tot_c, 8),
                                "cumulative_cost_usd": round(acc.cost_usd, 8) if acc else 0.0,
                            },
                        }
                        if isinstance(structured_output, dict):
                            structured_output["_cost"] = {
                                "tokens_in": node_tin,
                                "tokens_out": node_tout,
                                "cost_usd": round(tot_c, 6),
                            }
                    except Exception:
                        pass

                # Record outcome in the span: update() then end()
                if span:
                    try:
                        span.update(
                            output=structured_output or str(result)[:300],
                            level="DEFAULT",
                            status_message="success",
                            **cost_kwargs,
                        )
                        span.end()
                    except Exception:
                        pass

                return result

            except Exception as e:
                # Record error — never let tracing crash execution
                if span:
                    try:
                        node_tin = (acc.tokens_in - tin_before) if acc else 0
                        node_tout = (acc.tokens_out - tout_before) if acc else 0
                        cost_kwargs = {}
                        if node_tin > 0 or node_tout > 0:
                            from src.agent.cost_tracking import calculate_cost
                            in_c, out_c, tot_c = calculate_cost(node_tin, node_tout)
                            cost_kwargs = {
                                "usage_details": {"input": node_tin, "output": node_tout, "total": node_tin + node_tout},
                                "cost_details": {"input": round(in_c, 8), "output": round(out_c, 8), "total": round(tot_c, 8)},
                                "metadata": {"node_cost_usd": round(tot_c, 8)},
                            }

                        if _is_graph_pause(e):
                            span.update(level="DEFAULT", status_message="paused for human approval", **cost_kwargs)
                        else:
                            span.update(
                                level="ERROR",
                                status_message=f"{type(e).__name__}: {e}",
                                **cost_kwargs,
                            )
                        span.end()
                    except Exception:
                        pass
                raise
            finally:
                elapsed = time.perf_counter() - start
                logger.debug(f"[tracing] node={name} latency={elapsed:.3f}s")
                # Reset span context so sibling nodes don't inherit this span
                if span_token is not None:
                    _current_span_id.reset(span_token)

        return wrapper
    return decorator


try:
    from langfuse.langchain import CallbackHandler
except ImportError:
    CallbackHandler = None

# ---------------------------------------------------------------------------
# Structured node I/O helpers — keep Langfuse readable per node
# ---------------------------------------------------------------------------

def _truncate(text: Any, max_chars: int = 400) -> str:
    """Convert to string and truncate for display."""
    s = str(text) if text is not None else ""
    return s[:max_chars] + "…" if len(s) > max_chars else s


def build_node_input(node_name: str, state: dict) -> dict:
    """
    Return a small, human-readable dict of the fields that actually matter
    for *this* node.  Sent as `input` to the Langfuse span so reviewers see
    context without wading through the full 50-key state dict.
    """
    payload = state.get("incident_payload") or {}
    outputs  = state.get("outputs") or {}

    common = {
        "incident_number": payload.get("number") or payload.get("sys_id", "—"),
        "short_description": _truncate(payload.get("short_description"), 200),
    }

    extras: dict = {}

    if node_name == "formulate_query":
        extras = {
            "description": _truncate(payload.get("description"), 300),
            "human_solution": _truncate(state.get("human_solution"), 200),
        }

    elif node_name == "classify":
        extras = {
            "description": _truncate(payload.get("description"), 300),
            "human_solution_present": bool(state.get("human_solution")),
        }

    elif node_name == "retrieve":
        extras = {
            "search_query": _truncate(state.get("search_query"), 300),
            "category": payload.get("category", "—"),
            "service": payload.get("business_service") or payload.get("service", "—"),
        }

    elif node_name == "diagnose":
        evidence = state.get("retrieved_evidence") or []
        extras = {
            "evidence_count": len(evidence),
            "evidence_ids": [e.get("id") for e in evidence[:5]],
            "description": _truncate(payload.get("description"), 200),
        }

    elif node_name == "generate":
        evidence = state.get("retrieved_evidence") or []
        extras = {
            "diagnosis": _truncate(outputs.get("diagnosis"), 300),
            "evidence_count": len(evidence),
            "is_revision": bool(state.get("critic_verdict")),
            "revision_count": state.get("revision_count", 0),
        }

    elif node_name == "safety_check":
        extras = {
            "resolution_preview": _truncate(outputs.get("resolution"), 300),
        }

    elif node_name == "confidence_check":
        extras = {
            "confidence": state.get("confidence", "—"),
            "critic_exhausted": state.get("critic_exhausted", False),
        }

    elif node_name == "act":
        extras = {
            "resolution_preview": _truncate(
                outputs.get("resolution") or state.get("human_solution"), 300
            ),
            "human_decision": (state.get("human_decision") or {}).get("decision", "none"),
            "risk": state.get("risk", "—"),
        }

    elif node_name == "knowledge_capture":
        extras = {
            "human_solution": _truncate(state.get("human_solution"), 300),
            "classification": state.get("classification", "—"),
        }

    return {**common, **extras}


def build_node_output(node_name: str, result: dict) -> dict:
    """
    Return a concise, readable dict from the node's return value.
    """
    if not isinstance(result, dict):
        return {"raw": _truncate(result)}

    outputs = result.get("outputs") or {}

    if node_name == "formulate_query":
        return {
            "search_query": _truncate(result.get("search_query"), 400),
        }

    elif node_name == "classify":
        return {
            "classification": result.get("classification", "—"),
        }

    elif node_name == "retrieve":
        evidence = result.get("retrieved_evidence") or []
        return {
            "evidence_count": len(evidence),
            "top_ids_scores": [
                {"id": e.get("id"), "score": round(e.get("score", 0), 3)}
                for e in evidence[:5]
            ],
            "cache_hit": result.get("retrieval_cache_hit", False),
            "retrieval_failed": result.get("retrieval_failed", False),
        }

    elif node_name == "diagnose":
        return {
            "root_cause": _truncate(outputs.get("diagnosis"), 300),
            "confidence": (outputs.get("diagnosis_structured") or {}).get("confidence", "—"),
            "supporting_evidence": (outputs.get("diagnosis_structured") or {}).get(
                "supporting_evidence", []
            ),
        }

    elif node_name == "generate":
        return {
            "resolution_preview": _truncate(outputs.get("resolution"), 400),
            "revision_count": result.get("revision_count", 0),
        }

    elif node_name == "safety_check":
        return {
            "action_taken": result.get("action_taken", "—"),
            "safety_passed": result.get("action_taken") != "blocked_by_guardrail",
        }

    elif node_name == "confidence_check":
        return {
            "confidence": result.get("confidence", "—"),
            "critic_exhausted": result.get("critic_exhausted", False),
        }

    elif node_name == "act":
        return {
            "action_taken": result.get("action_taken", "—"),
            "servicenow_write": result.get("servicenow_write", "—"),
        }

    elif node_name == "knowledge_capture":
        return {
            "status": (result.get("knowledge_capture_result") or {}).get("status", "—"),
            "article_number": (result.get("knowledge_capture_result") or {}).get(
                "article_number", "—"
            ),
        }

    # Generic fallback — pick only leaf-level keys that are simple types
    return {
        k: _truncate(v)
        for k, v in result.items()
        if isinstance(v, (str, int, float, bool)) and k != "outputs"
    }


def get_llm_callback():

    """
    Returns a LangChain CallbackHandler list tied to the current node span.

    Links the Langfuse handler to both the root trace_id AND the current node's
    span_id (parent_observation_id), so LLM generations appear nested
    under their node span in Langfuse:
        trace → node span → LLM generation

    Also appends the cost-tracking callback so every LLM call in every node
    automatically accumulates token usage against the current execution_id.
    """
    callbacks: list = []

    # --- Langfuse tracing callback ---
    if LANGFUSE_AVAILABLE and CallbackHandler is not None:
        parent_trace_id = _current_trace_id.get()
        parent_span_id = _current_span_id.get()

        trace_context = {}
        if parent_trace_id:
            trace_context["trace_id"] = parent_trace_id
        if parent_span_id:
            trace_context["parent_span_id"] = parent_span_id

        try:
            handler = CallbackHandler(
                trace_context=trace_context if trace_context else None
            )
            callbacks.append(handler)
        except Exception:
            pass

    # --- Cost-tracking callback ---
    # Imported here (not at module level) to keep tracing.py free of circular deps.
    try:
        from src.agent.cost_tracking import get_cost_callback as _get_cost_cb
        # The execution_id flows through state["execution_id"] and is stamped on
        # the root trace_id seed (see trace_execution). We mirror that here by
        # re-deriving it from the trace_id context var which is always set when
        # a node runs inside a tracked execution.  Nodes that call get_llm()
        # directly (e.g. in tests) without a trace context simply get no cost cb.
        execution_id = _current_execution_id.get()
        if execution_id:
            callbacks.extend(_get_cost_cb(execution_id))
    except Exception:
        pass  # never break LLM calls due to cost tracking issues

    return callbacks
