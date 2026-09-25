import os
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

try:
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    PostgresSaver = object
    ConnectionPool = object

# One saver (and connection pool) per process: the API reads checkpoints on every request
_checkpointer = None
_lock = threading.Lock()


def _build_checkpointer() -> Any:
    if not LANGGRAPH_AVAILABLE:
        logger.warning("langgraph-checkpoint-postgres not found, using MockCheckpointer")
        class MockCheckpointer:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def get(self, config): return None
            def put(self, config, checkpoint, metadata): pass
            def setup(self): pass
        return MockCheckpointer()

    conn_string = os.environ.get("PG_CONN_STRING")
    if not conn_string:
        raise ValueError("Environment variable 'PG_CONN_STRING' is not set")
    pool = ConnectionPool(
        conninfo=conn_string,
        max_size=20,
        kwargs={"autocommit": True},
    )
    checkpointer = PostgresSaver(pool)
    # Create checkpoint tables/indexes on first run (requires autocommit
    # because CREATE INDEX CONCURRENTLY cannot run inside a transaction).
    try:
        checkpointer.setup()
    except Exception:
        pool.close()  # otherwise the failed pool keeps reconnecting in the background
        raise
    return checkpointer


def get_checkpointer() -> Any:
    global _checkpointer
    if _checkpointer is None:
        with _lock:
            if _checkpointer is None:
                _checkpointer = _build_checkpointer()
    return _checkpointer


def thread_config(execution_id: str) -> dict:
    return {"configurable": {"thread_id": execution_id}}


def get_run_state(graph: Any, execution_id: str) -> Any:
    """Checkpointed state of a run, or None if nothing was ever saved for it"""
    snapshot = graph.get_state(thread_config(execution_id))
    if not snapshot.values:
        return None
    return snapshot


def is_paused(snapshot: Any) -> bool:
    """True while the run waits at interrupt for a human decision"""
    return snapshot is not None and "interrupt" in snapshot.next
