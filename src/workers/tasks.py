import os
from celery import Celery
from src.agent.graph import compile_graph
from src.agent.checkpointer import get_checkpointer
from src.observability.tracing import trace_execution

class GraphAgentExecutor:
    """
    Implements the agent execution seam expected by the Celery worker infrastructure.
    Matches the PendingAgentExecutorForTests protocol from feature/s2.3-celery-workers.
    """
    
    @trace_execution(name="execute_incident_graph")
    def execute(self, accepted_incident: dict, execution_id: str = None, incident_number: str = None) -> dict:
        """
        Execute the LangGraph state machine.
        """
        # If execution_id/incident_number are passed as args, trace_execution will pick them up
        # If not, try to extract them from accepted_incident
        exec_id = execution_id or accepted_incident.get("execution_id", "default_exec_id")
        inc_num = incident_number or accepted_incident.get("number") or accepted_incident.get("sys_id", "UNKNOWN_INC")
        
        checkpointer = get_checkpointer()
        graph = compile_graph(checkpointer=checkpointer)
        
        initial_state = {
            "execution_id": exec_id,
            "incident_number": inc_num,
            "incident_payload": accepted_incident
        }
        
        config = {"configurable": {"thread_id": exec_id}}
        
        # Run the graph
        result = graph.invoke(initial_state, config=config)
        return result


# ---------------------------------------------------------------------------
# Temporary standalone task for local use until S2.3 branch is fully merged
# ---------------------------------------------------------------------------
redis_url = os.environ.get("REDIS_URL")
app = Celery('tasks', broker=redis_url, backend=redis_url)

@app.task(bind=True, name="execute_incident_graph")
def execute_incident_graph(self, execution_id: str, incident_number: str, initial_payload: dict):
    """
    Execute the LangGraph state machine inside the Celery worker.
    Uses the Postgres checkpointer for state persistence.
    """
    executor = GraphAgentExecutor()
    return executor.execute(
        accepted_incident=initial_payload,
        execution_id=execution_id,
        incident_number=incident_number
    )
