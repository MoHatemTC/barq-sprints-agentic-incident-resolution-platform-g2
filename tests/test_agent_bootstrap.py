import pytest
from src.agent.llm import get_llm, get_embeddings
from src.agent.checkpointer import get_checkpointer

def test_llm_singletons():
    llm1 = get_llm()
    llm2 = get_llm()
    assert llm1 is llm2
    
    emb1 = get_embeddings()
    emb2 = get_embeddings()
    assert emb1 is emb2

def test_checkpointer_initialization():
    checkpointer = get_checkpointer()
    assert checkpointer is not None
