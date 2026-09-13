from types import SimpleNamespace

from src.retrieval.sources import servicenow_source


def test_article_from_servicenow_uses_configured_metadata_columns(monkeypatch):
    monkeypatch.setattr(
        servicenow_source,
        "SERVICENOW",
        SimpleNamespace(
            kb_metadata_field_map={
                "service": "u_service",
                "version": "u_version",
                "security_level": "u_security_level",
                "article_number": "u_article_number",
            }
        ),
    )

    article = servicenow_source.article_from_servicenow(
        {
            "sys_id": "article-sys-id",
            "number": "KB0099999",
            "u_article_number": "KB0010",
            "short_description": "VPN outage",
            "text": "Restart the VPN client.",
            "kb_category": {"value": "network-category-sys-id"},
            "u_service": "corporate-vpn",
            "workflow_state": "published",
            "u_version": "3",
            "u_security_level": "internal",
        }
    )

    assert article.sys_id == "article-sys-id"
    assert article.number == "KB0010"
    assert article.article_id == "KB0010"
    assert article.category == "network-category-sys-id"
    assert article.service == "corporate-vpn"
    assert article.version == 3
    assert article.security_level == "internal"
