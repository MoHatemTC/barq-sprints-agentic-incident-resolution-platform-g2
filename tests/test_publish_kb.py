from types import SimpleNamespace

import pytest

from src.retrieval import publish_kb
from src.retrieval.schema import Article
from src.servicenow.exceptions import ServiceNowWriteNotAppliedError


def _article(workflow_state="published"):
    return Article(
        sys_id="",
        number="KB0010",
        article_id="KB0010",
        title="VPN outage",
        body="Restart the VPN client.",
        category="network",
        service="network",
        workflow_state=workflow_state,
        version=1,
        security_level="internal",
    )


class _Response:
    def __init__(self, result):
        self._result = result

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": self._result}


class _FakeAuth:
    def auth_headers(self):
        return {"Authorization": "Bearer token"}


class _FakeClient:
    def __init__(self, *, write_result, read_result):
        self.write_result = write_result
        self.read_result = read_result
        self.posts = []
        self.patches = []
        self.gets = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _Response(self.write_result)

    def patch(self, url, **kwargs):
        self.patches.append((url, kwargs))
        return _Response(self.write_result)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return _Response(self.read_result)


# Fixed sys_id used across tests -- stands in for whatever the real
# data/kb_category_mapping.json happens to contain, so these tests don't
# depend on (or break when someone edits) that file.
_NETWORK_CATEGORY_SYS_ID = "fake-network-category-sys-id"


@pytest.fixture
def publish_env(monkeypatch):
    saved = []
    monkeypatch.setattr(
        publish_kb,
        "SERVICENOW",
        SimpleNamespace(
            instance_url="https://example.service-now.com",
            kb_table="kb_knowledge",
            kb_sys_id="",
        ),
    )
    monkeypatch.setattr(publish_kb, "ServiceNowOAuthClient", _FakeAuth)
    monkeypatch.setattr(publish_kb, "load_articles_from_json", lambda path: [_article()])
    monkeypatch.setattr(publish_kb, "save_mapping", lambda mapping: saved.append(dict(mapping)))
    monkeypatch.setattr(
        publish_kb, "_CATEGORY_MAPPING", {"network": _NETWORK_CATEGORY_SYS_ID}
    )
    return saved


def _install_client(monkeypatch, fake_client):
    monkeypatch.setattr(publish_kb.httpx, "Client", lambda timeout: fake_client)


def test_post_read_back_matching_workflow_state_succeeds(monkeypatch, publish_env):
    read_back = {
        "short_description": "VPN outage",
        "text": "Restart the VPN client.",
        "kb_category": _NETWORK_CATEGORY_SYS_ID,
        "workflow_state": "published",
    }
    fake_client = _FakeClient(write_result={"sys_id": "new-sys-id"}, read_result=read_back)
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {})

    stats = publish_kb.publish()

    assert stats == {"created": 1, "updated": 0, "failed": []}
    assert publish_env == [{"KB0010": "new-sys-id"}]
    assert len(fake_client.posts) == 1
    assert len(fake_client.gets) == 1


def test_patch_read_back_matching_workflow_state_succeeds(monkeypatch, publish_env):
    read_back = {
        "short_description": "VPN outage",
        "text": "Restart the VPN client.",
        "kb_category": _NETWORK_CATEGORY_SYS_ID,
        "workflow_state": "published",
    }
    fake_client = _FakeClient(write_result={"sys_id": "existing-sys-id"}, read_result=read_back)
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {"KB0010": "existing-sys-id"})

    stats = publish_kb.publish()

    assert stats == {"created": 0, "updated": 1, "failed": []}
    assert publish_env == [{"KB0010": "existing-sys-id"}]
    assert len(fake_client.patches) == 1
    assert len(fake_client.gets) == 1


def test_post_read_back_draft_workflow_state_raises(monkeypatch, publish_env):
    read_back = {
        "short_description": "VPN outage",
        "text": "Restart the VPN client.",
        "kb_category": _NETWORK_CATEGORY_SYS_ID,
        "workflow_state": "draft",
    }
    fake_client = _FakeClient(write_result={"sys_id": "new-sys-id"}, read_result=read_back)
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {})

    with pytest.raises(ServiceNowWriteNotAppliedError, match="workflow_state"):
        publish_kb.publish()

    assert publish_env == []
    assert len(fake_client.posts) == 1
    assert len(fake_client.gets) == 1


def test_patch_read_back_draft_workflow_state_raises(monkeypatch, publish_env):
    read_back = {
        "short_description": "VPN outage",
        "text": "Restart the VPN client.",
        "kb_category": _NETWORK_CATEGORY_SYS_ID,
        "workflow_state": "draft",
    }
    fake_client = _FakeClient(write_result={"sys_id": "existing-sys-id"}, read_result=read_back)
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {"KB0010": "existing-sys-id"})

    with pytest.raises(ServiceNowWriteNotAppliedError, match="workflow_state"):
        publish_kb.publish()

    assert publish_env == []
    assert len(fake_client.patches) == 1
    assert len(fake_client.gets) == 1