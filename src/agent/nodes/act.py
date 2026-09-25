import logging
import os
from typing import Dict, Any
from src.observability.tracing import trace_node

logger = logging.getLogger(__name__)

AGENT_NAME = "barq-agent"


class ExecutionLogNotWritten(Exception):
    """The receipt row did not land; retry act so it gets written."""

    retryable = True


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


def get_servicenow_client():
    # Lazy import: tests swap this out for a fake client
    from src.servicenow.client import ServiceNowClient
    return ServiceNowClient()


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


@trace_node(name="act")
def act_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Final act node: write the outcome to ServiceNow exactly once """
    plan = _plan_write(state)
    outcome = {"action_taken": plan["action_taken"]}

    sys_id = (state.get("incident_payload") or {}).get("sys_id")
    if not sys_id:
        return {**outcome, "servicenow_write": "skipped_no_sys_id"}

    execution_id = state.get("execution_id")
    client = get_servicenow_client()

    if client.find_execution_log(execution_id, plan["action"]):
        return {**outcome, "servicenow_write": "already_done"}

    demo_crash("before_write", execution_id)
    client.update_incident(sys_id, plan["fields"])
    receipt = client.write_execution_log(
        sys_id,
        execution_id,
        plan["action"],
        plan["status"],
        agent=AGENT_NAME,
        result=plan["result"],
    )
    if receipt is None:
        raise ExecutionLogNotWritten(f"execution log not written for {execution_id}")
    demo_crash("after_write", execution_id)

    return {**outcome, "servicenow_write": "written"}
