import importlib
import json
from types import SimpleNamespace

import pytest


class _StubLLM:
    def __init__(self, reply):
        self.reply = reply

    def invoke(self, prompt, **kwargs):
        return SimpleNamespace(content=self.reply)


# One fixed reply per agent: a valid ticket whose cited draft passes the critic.
_HERMETIC_REPLIES = {
    "validate": "valid",
    "classify": "network",
    "diagnose": json.dumps({
        "root_cause": "Cached VPN credentials are stale.",
        "reasoning": "KB0001 describes the same failure.",
        "supporting_evidence": ["KB0001"],
        "confidence": 0.9,
    }),
    "generate": "1. Reset the VPN credentials. [Source: KB0001]",
    "verify_evidence": json.dumps({
        "passed": True, "feedback": "", "invalid_steps": [], "citation_findings": [],
    }),
}


@pytest.fixture
def hermetic_llm(monkeypatch):
    from src.agent import llm

    monkeypatch.setattr(llm, "_llm_instance", llm.MockLLM())
    for node, reply in _HERMETIC_REPLIES.items():
        monkeypatch.setattr(f"src.agent.nodes.{node}.get_llm", lambda r=reply: _StubLLM(r))


@pytest.fixture(autouse=True)
def _reload_embedding_module_after_test():
    """Undo any monkeypatch+reload pollution left behind by tests that
    reload src.config / src.retrieval.embedding with fake env vars
    (e.g. tests/test_embedding.py). Runs after every test.
    """
    yield
    import src.config as cfg_mod
    import src.retrieval.embedding as emb_mod
    importlib.reload(cfg_mod)
    importlib.reload(emb_mod)
