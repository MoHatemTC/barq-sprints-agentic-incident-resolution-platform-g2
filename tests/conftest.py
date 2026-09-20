import importlib

import pytest


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
