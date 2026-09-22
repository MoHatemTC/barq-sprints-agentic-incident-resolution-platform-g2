# it is unit testing and to run it use "pytest tests/test_filters.py -v"
from src.retrieval.filters import RetrievalFilters, build_qdrant_filter


def keys(conditions) -> dict:
    """payload_key: match so tests can read a filter like a dict"""
    return {c.key: c.match for c in conditions}


def test_default_allows_published_and_blocks_restricted_and_marker():
    f = build_qdrant_filter()
    assert keys(f.must)["workflow_state"].any == ["published"]
    assert keys(f.must_not)["security_level"].any == ["restricted"]
    assert keys(f.must_not)["_is_marker"].value is True


def test_optional_fields_are_off_by_default():
    must = keys(build_qdrant_filter().must)
    assert "category" not in must and "service" not in must and "version" not in must


def test_each_optional_field_adds_one_and_condition():
    f = build_qdrant_filter(RetrievalFilters(category="network", service="corporate-vpn", version=2))
    must = keys(f.must)
    assert must["category"].value == "network"
    assert must["service"].value == "corporate-vpn"
    assert must["version"].value == 2
    assert must["workflow_state"].any == ["published"]  # base rule still there


def test_custom_lists_are_used():
    f = build_qdrant_filter(RetrievalFilters(
        allowed_workflow_states=("published", "draft"),
        blocked_security_levels=("restricted", "confidential"),
    ))
    assert keys(f.must)["workflow_state"].any == ["published", "draft"]
    assert keys(f.must_not)["security_level"].any == ["restricted", "confidential"]


def test_empty_blocked_list_drops_security_condition():
    f = build_qdrant_filter(RetrievalFilters(blocked_security_levels=()))
    assert "security_level" not in keys(f.must_not)
