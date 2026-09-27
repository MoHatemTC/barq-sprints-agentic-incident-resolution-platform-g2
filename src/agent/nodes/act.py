import logging
import os
from typing import Any, Dict, Optional

from src.agent.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRefusal
from src.observability.tracing import trace_node

logger = logging.getLogger(__name__)

AGENT_NAME = "barq-agent"

# The registry act_node dispatches through. Held as a module attribute rather
# than referenced inline so a test can substitute a registry built over a fake
# ServiceNowClient; IncidentGateway accepts an injected client, so that fake
# still exercises the real registry, the real permission checks and the real
# gateway, and only the network is faked.
TOOL_REGISTRY = DEFAULT_TOOL_REGISTRY


class ExecutionLogNotWritten(Exception):
    """The receipt row did not land; retry act so it gets written."""

    retryable = True


class ToolRefused(Exception):
    """The registry refused a ServiceNow write, so act halts without writing.

    Terminal, not retryable. A refusal means the tool is unregistered or its
    permission class is not approved for this execution, and neither changes
    by retrying the same execution -- so this dead-letters through the worker
    retry policy instead of burning retries. It is deliberately *not* the
    same path as a successful write: the caller must never see a refusal
    reported as ``servicenow_write="written"``.
    """

    retryable = False

    def __init__(self, refusal: ToolRefusal):
        self.tool = refusal.tool
        self.reason = refusal.reason
        self.refusal = refusal
        super().__init__(
            f"ServiceNow write halted: tool '{refusal.tool}' refused "
            f"({refusal.reason}) for execution "
            f"'{refusal.execution_id}': {refusal.message}"
        )


def _first_demo_crash(execution_id: str, point: str) -> bool:
    # Redis marker so the recovered attempt does not die again; no Redis = no crash
    try:
        import redis
        client = redis.Redis.from_url(os.environ["REDIS_URL"])
        return bool(client.set(f"demo_crash:{point}:{execution_id}", 1, nx=True, ex=86400))
    except Exception:
        return False


def demo_crash(point: str, execution_id: str) -> None:
    """DEMO ONLY (S3.4 crash-recovery proof): kill the worker process at an exact point.

    Off unless DEMO_CRASH_AT=before_write|after_write. os._exit is a real process
    death (like kill -9), not an exception; it fires once per execution.
    """
    if os.environ.get("DEMO_CRASH_AT") != point:
        return
    if not _first_demo_crash(execution_id, point):
        return
    logger.warning(f"DEMO_CRASH_AT={point}: killing worker for execution {execution_id}")
    logging.shutdown()
    os._exit(137)


def _plan_write(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the outcome, the incident fields and the log row for this run."""
    decision = state.get("human_decision")
    outputs = state.get("outputs") or {}

    if decision is not None and decision.get("decision") != "approve":
        reviewer = decision.get("reviewer") or "unknown reviewer"
        reason = f"Rejected by {reviewer}"
        if decision.get("comment"):
            reason += f": {decision['comment']}"
        return {
            "action_taken": "rejected_by_human",
            "action": "escalated_rejected",
            "status": "blocked",
            "fields": {"human_review": True, "failure_reason": reason},
            "result": reason,
        }

    fields = {"processing_state": "complete", "human_review": False}
    if outputs.get("resolution"):
        fields["resolution"] = outputs["resolution"]
    if state.get("confidence") is not None:
        fields["confidence"] = state["confidence"]
    if state.get("classification"):
        fields["classification"] = state["classification"]

    if decision is None:
        return {
            "action_taken": "resolved_automatically",
            "action": "auto_resolve",
            "status": "succeeded",
            "fields": fields,
            "result": "Resolved automatically",
        }
    return {
        "action_taken": "approved_by_human",
        "action": "approved_resolve",
        "status": "succeeded",
        "fields": fields,
        "result": f"Approved by {decision.get('reviewer') or 'unknown reviewer'}",
    }


def _dispatch(registry, tool: str, execution_id: Optional[str], **kwargs):
    """dispatch() and refuse-on-refusal, in one place.

    Every ServiceNow call act_node makes goes through here, so there is no
    path by which a refusal can be mistaken for a completed write. dispatch
    signals refusal by *returning* a ToolRefusal rather than raising, which is
    easy to drop on the floor; a caller that forgot to check would report
    ``servicenow_write="written"`` having written nothing.
    """
    result = registry.dispatch(tool, execution_id, **kwargs)
    if isinstance(result, ToolRefusal):
        logger.error(
            "Registry refused tool=%s reason=%s execution_id=%s: %s",
            result.tool, result.reason, result.execution_id, result.message,
        )
        raise ToolRefused(result)
    return result


@trace_node(name="act")
def act_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Final act node: write the outcome to ServiceNow exactly once.

    Every ServiceNow call is a registry dispatch, so the permission class
    decides whether it happens at all: the incident write and its receipt are
    LOW_RISK_WRITE, the idempotency probe is READ. A refusal raises
    ToolRefused and the write does not happen -- it is never reported as
    written.
    """
    plan = _plan_write(state)
    outcome = {"action_taken": plan["action_taken"]}

    sys_id = (state.get("incident_payload") or {}).get("sys_id")
    if not sys_id:
        return {**outcome, "servicenow_write": "skipped_no_sys_id"}

    execution_id = state.get("execution_id")

    # Idempotency probe (READ, no approval needed). dispatch supplies
    # execution_id, so it is never passed as a kwarg here.
    if _dispatch(TOOL_REGISTRY, "find_execution_log", execution_id,
                 action=plan["action"]):
        return {**outcome, "servicenow_write": "already_done"}

    demo_crash("before_write", execution_id)
    _dispatch(TOOL_REGISTRY, "write_ai_fields", execution_id,
              sys_id=sys_id, fields=plan["fields"])
    receipt = _dispatch(
        TOOL_REGISTRY, "write_execution_log", execution_id,
        incident_sys_id=sys_id,
        action=plan["action"],
        status=plan["status"],
        agent=AGENT_NAME,
        result=plan["result"],
    )
    if receipt is None:
        raise ExecutionLogNotWritten(f"execution log not written for {execution_id}")
    demo_crash("after_write", execution_id)

    return {**outcome, "servicenow_write": "written"}
