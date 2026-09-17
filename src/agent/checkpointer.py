import os
import logging
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

def get_checkpointer() -> Any:
    if not LANGGRAPH_AVAILABLE:
        logger.warning("langgraph-checkpoint-postgres not found, using MockCheckpointer")
        class MockCheckpointer:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def get(self, config): return None
            def put(self, config, checkpoint, metadata): pass
            def setup(self): pass
        return MockCheckpointer()

    conn_string = os.environ.get("PG_CONN_STRING", "postgresql://postgres:postgres@localhost:5432/postgres")
    pool = ConnectionPool(conninfo=conn_string, max_size=20)
    checkpointer = PostgresSaver(pool)
    return checkpointer
