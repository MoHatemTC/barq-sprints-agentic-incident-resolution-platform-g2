from types import SimpleNamespace

import httpx
import pytest

from src.retrieval import publish_kb
from src.retrieval.schema import Article
from src.servicenow.exceptions import ServiceNowWriteNotAppliedError


_NETWORK_CATEGORY_SYS_ID = "fake-network-category-sys-id"


def _article(workflow_state="published"):
    return Article(
        sys_id="test_sys_id_123",
        number="KB0010",
        article_id="KB0010",
        title="VPN outage",
        body="Restart the VPN client.",
        category="network",
        service="network",
        workflow_state=workflow_state,
        version=3,
        security_level="internal",
    )


class _Response:
    def __init__(self, result):
        self._result = result

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": self._result}


class _ErrorResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        request = httpx.Request("GET", "https://example.service-now.com/record")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("request failed", request=request, response=response)


class _FakeAuth:
    def auth_headers(self):
        return {"Authorization": "Bearer token"}


class _FakeClient:
    def __init__(self, *, write_result, read_results, action_result=None):
        self.write_result = write_result
        self.read_results = list(read_results)
        self.action_result = action_result if action_result is not None else {}
        self.posts = []
        self.patches = []
        self.gets = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if "/api/now/table/" in url:
            return _Response(self.write_result)
        return _Response(self.action_result)

    def patch(self, url, **kwargs):
        self.patches.append((url, kwargs))
        return _Response(self.write_result)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        result = self.read_results.pop(0)
        return result if isinstance(result, _ErrorResponse) else _Response(result)


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
            kb_metadata_field_map={},
            kb_publish_action_path="",
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


def _matching_read_back(workflow_state="published", **metadata):
    return {
        "short_description": "VPN outage",
        "text": "Restart the VPN client.",
        "kb_category": _NETWORK_CATEGORY_SYS_ID,
        "workflow_state": workflow_state,
        **metadata,
    }


def test_post_read_back_matching_workflow_state_succeeds(monkeypatch, publish_env):
    fake_client = _FakeClient(
        write_result={"sys_id": "new-sys-id"},
        read_results=[_matching_read_back()],
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {})

    stats = publish_kb.publish()

    assert stats == {"created": 1, "updated": 0, "skipped_unchanged": 0, "failed": []}
    assert publish_env == [{"KB0010": "new-sys-id"}]
    assert len(fake_client.posts) == 1
    assert len(fake_client.gets) == 1


def test_patch_read_back_matching_workflow_state_succeeds(monkeypatch, publish_env):
    fake_client = _FakeClient(
        write_result={"sys_id": "existing-sys-id"},
        read_results=[
            _matching_read_back(short_description="Old title"),
            _matching_read_back(),
        ],
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {"KB0010": "existing-sys-id"})

    stats = publish_kb.publish()

    assert stats == {"created": 0, "updated": 1, "skipped_unchanged": 0, "failed": []}
    assert publish_env == [{"KB0010": "existing-sys-id"}]
    assert len(fake_client.patches) == 1
    assert len(fake_client.gets) == 2


def test_matching_mapped_record_is_skipped_without_patch(monkeypatch, publish_env):
    fake_client = _FakeClient(
        write_result={"sys_id": "existing-sys-id"},
        read_results=[_matching_read_back()],
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {"KB0010": "existing-sys-id"})

    stats = publish_kb.publish()

    assert stats == {"created": 0, "updated": 0, "skipped_unchanged": 1, "failed": []}
    assert publish_env == [{"KB0010": "existing-sys-id"}]
    assert not fake_client.patches
    assert len(fake_client.gets) == 1


def test_missing_mapped_record_is_recreated(monkeypatch, publish_env):
    fake_client = _FakeClient(
        write_result={"sys_id": "replacement-sys-id"},
        read_results=[_ErrorResponse(404), _matching_read_back()],
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {"KB0010": "stale-sys-id"})

    stats = publish_kb.publish()

    assert stats == {"created": 1, "updated": 0, "skipped_unchanged": 0, "failed": []}
    assert publish_env == [{"KB0010": "replacement-sys-id"}]
    assert len(fake_client.posts) == 1
    assert not fake_client.patches


@pytest.mark.parametrize("existing", [False, True])
def test_published_to_draft_without_action_fails_and_is_not_counted(
    monkeypatch, publish_env, existing
):
    sys_id = "existing-sys-id" if existing else "new-sys-id"
    fake_client = _FakeClient(
        write_result={"sys_id": sys_id},
        read_results=(
            [_matching_read_back(short_description="Old title"), _matching_read_back("draft")]
            if existing
            else [_matching_read_back("draft")]
        ),
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(
        publish_kb, "load_mapping", lambda: {"KB0010": sys_id} if existing else {}
    )

    with pytest.raises(ServiceNowWriteNotAppliedError, match="workflow_state"):
        publish_kb.publish()

    assert publish_env == []
    assert len(fake_client.gets) == 2 if existing else 1


def test_non_workflow_field_mismatch_fails_and_is_not_mapped(monkeypatch, publish_env):
    fake_client = _FakeClient(
        write_result={"sys_id": "new-sys-id"},
        read_results=[_matching_read_back(text="Different article text.")],
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {})

    with pytest.raises(ServiceNowWriteNotAppliedError, match="text"):
        publish_kb.publish()

    assert publish_env == []


def test_configured_publish_action_requires_final_published_read_back(monkeypatch, publish_env):
    monkeypatch.setattr(
        publish_kb.SERVICENOW,
        "kb_publish_action_path",
        "/api/x_scope/kb_publish/{sys_id}",
    )
    fake_client = _FakeClient(
        write_result={"sys_id": "new-sys-id"},
        read_results=[_matching_read_back("draft"), _matching_read_back("published")],
        action_result={"accepted": True},
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {})

    stats = publish_kb.publish()

    assert stats == {"created": 1, "updated": 0, "skipped_unchanged": 0, "failed": []}
    assert len(fake_client.posts) == 2
    assert fake_client.posts[1][0].endswith("/api/x_scope/kb_publish/new-sys-id")
    assert len(fake_client.gets) == 2


def test_configured_publish_action_that_stays_draft_fails(monkeypatch, publish_env):
    monkeypatch.setattr(
        publish_kb.SERVICENOW,
        "kb_publish_action_path",
        "/api/x_scope/kb_publish/{sys_id}",
    )
    fake_client = _FakeClient(
        write_result={"sys_id": "new-sys-id"},
        read_results=[_matching_read_back("draft"), _matching_read_back("draft")],
    )
    _install_client(monkeypatch, fake_client)
    monkeypatch.setattr(publish_kb, "load_mapping", lambda: {})

    with pytest.raises(ServiceNowWriteNotAppliedError, match="workflow_state"):
        publish_kb.publish()

    assert publish_env == []
    assert len(fake_client.posts) == 2
    assert len(fake_client.gets) == 2


def test_category_uses_mapped_reference_sys_id(monkeypatch, publish_env):
    payload = publish_kb._build_payload(_article())

    assert payload["kb_category"] == _NETWORK_CATEGORY_SYS_ID
    assert payload["kb_category"] != _article().category


def test_category_is_omitted_when_no_reference_mapping_exists(monkeypatch, publish_env):
    monkeypatch.setattr(publish_kb, "_CATEGORY_MAPPING", {})

    assert "kb_category" not in publish_kb._build_payload(_article())


def test_metadata_payload_uses_configured_servicenow_field_names(monkeypatch, publish_env):
    monkeypatch.setattr(
        publish_kb.SERVICENOW,
        "kb_metadata_field_map",
        {
            "service": "u_service",
            "version": "u_version",
            "security_level": "u_security_level",
            "article_number": "u_article_number",
        },
    )

    payload = publish_kb._build_payload(_article())

    assert payload["u_service"] == "network"
    assert payload["u_version"] == 3
    assert payload["u_security_level"] == "internal"
    assert payload["u_article_number"] == "KB0010"


def test_metadata_is_omitted_until_servicenow_field_names_are_configured(monkeypatch, publish_env):
    payload = publish_kb._build_payload(_article())

    assert set(payload) == {
        "short_description",
        "text",
        "workflow_state",
        "kb_category",
    }
def test_publish_article_creates_and_verifies_article(
    monkeypatch,
    publish_env,
):
    fake_client = _FakeClient(
        write_result={"sys_id": "human-resolution-sys-id"},
        read_results=[_matching_read_back()],
    )

    _install_client(monkeypatch, fake_client)

    monkeypatch.setattr(
        publish_kb,
        "load_mapping",
        lambda: {},
    )

    result = publish_kb.publish_article(_article())

    assert result == {
        "status": "created",
        "article_number": "KB0010",
        "sys_id": "human-resolution-sys-id",
    }

    # ServiceNow write happened.
    assert len(fake_client.posts) == 1

    # Critical: fresh read-back verification happened.
    assert len(fake_client.gets) == 1

    # The mapping is saved only after verification succeeds.
    assert publish_env == [
        {"KB0010": "human-resolution-sys-id"}
    ]