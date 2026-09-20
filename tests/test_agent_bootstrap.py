import pytest
from unittest.mock import patch, MagicMock
from src.agent.llm import get_llm, get_embeddings
from src.agent.checkpointer import get_checkpointer

def test_llm_singletons():
    llm1 = get_llm()
    llm2 = get_llm()
    assert llm1 is llm2
    
    emb1 = get_embeddings()
    emb2 = get_embeddings()
    assert emb1 is emb2
    
@patch("src.agent.checkpointer.PostgresSaver")
@patch("src.agent.checkpointer.ConnectionPool")
def test_checkpointer_initialization(mock_pool_cls, mock_saver_cls):
    import os
    os.environ["PG_CONN_STRING"] = "postgresql://test:test@localhost:5432/test"
    
    mock_instance = MagicMock()
    mock_saver_cls.return_value = mock_instance
    
    checkpointer = get_checkpointer()
    assert checkpointer is not None
    mock_instance.setup.assert_called_once()
