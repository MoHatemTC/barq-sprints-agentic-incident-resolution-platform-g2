import os
from celery import Celery
from src.agent.graph import compile_graph
from src.agent.checkpointer import get_checkpointer
from src.observability.tracing import trace_execution

# Initialize Celery
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
app = Celery('tasks', broker=redis_url, backend=redis_url)

@app.task(bind=True, name="execute_incident_graph")
@trace_execution(name="execute_incident_graph")
def execute_incident_graph(self, execution_id: str, incident_number: str, initial_payload: dict):
    """
    Execute the LangGraph state machine inside the Celery worker.
    Uses the Postgres checkpointer for state persistence.
    """
    checkpointer = get_checkpointer()
    graph = compile_graph(checkpointer=checkpointer)
    
    initial_state = {
        "execution_id": execution_id,
        "incident_number": incident_number,
        "incident_payload": initial_payload
    }
    
    config = {"configurable": {"thread_id": execution_id}}
    
    # Run the graph
    result = graph.invoke(initial_state, config=config)
    
    return result
