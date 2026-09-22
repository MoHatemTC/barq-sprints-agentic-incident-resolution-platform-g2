from unittest.mock import patch
from types import SimpleNamespace
from src.retrieval.sources import servicenow_source # Ensure this import matches your file

@patch("src.retrieval.sources.servicenow_source._category_names")
def test_article_from_servicenow_uses_configured_metadata_columns(mock_category_names, monkeypatch):
    # 1. Force the function to return our mock dictionary without touching the filesystem
    mock_category_names.return_value = {"network-category-sys-id": "network"}
    
    # 2. Setup your metadata field map
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

    # 3. Execute the function
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
    
    # 4. Assertions to prove it worked
    assert article.category == "network"
    assert article.service == "corporate-vpn"