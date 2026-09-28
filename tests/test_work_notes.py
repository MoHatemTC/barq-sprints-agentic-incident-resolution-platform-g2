"""Tests for Human-in-the-Loop work notes written to ServiceNow.

Covers three layers:
  1. _human_review_note() — note content formatting in act.py
  2. IncidentGateway.add_work_note() / ServiceNowClient.add_work_note() — write + verification logic
  3. act_node integration — write_work_note is dispatched correctly and
     ServiceNowWriteNotAppliedError is treated as a warning (non-fatal).
"""

from unittest.mock import MagicMock, Mock, call, patch

import pytest

from src.agent.nodes.act import _human_review_note, act_node
from src.agent.tools.permissions import PermissionClass
from src.agent.tools.registry import ToolRegistry
from src.servicenow import exceptions as exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(
    *,
    find_log=None,       # return value for find_execution_log
    write_ai=None,       # return value / side_effect for write_ai_fields
    write_note=None,     # return value / side_effect for write_work_note
    write_log=None,      # return value for write_execution_log
):
    """Build a ToolRegistry backed entirely by mocks."""
    registry = ToolRegistry()
    registry.register("find_execution_log", PermissionClass.READ,
                      Mock(return_value=find_log))
    registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE,
                      Mock(side_effect=write_ai) if isinstance(write_ai, Exception) else Mock(return_value=write_ai))
    registry.register("write_work_note", PermissionClass.LOW_RISK_WRITE,
                      Mock(side_effect=write_note) if isinstance(write_note, Exception) else Mock(return_value=write_note))
    registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE,
                      Mock(return_value=write_log or {"sys_id": "log_row"}))
    return registry


def _base_state(**overrides):
    """Minimal graph state for act_node — no human review by default."""
    state = {
        "execution_id": "exec-test-001",
        "incident_payload": {"sys_id": "INC0001_sys_id"},
        "outputs": {"resolution": "Server was restarted.", "diagnosis": "High CPU usage."},
        "confidence": 0.92,
        "classification": "infrastructure",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# 1. _human_review_note() — formatting
# ---------------------------------------------------------------------------

class TestHumanReviewNote:

    def test_no_decision_returns_empty(self):
        assert _human_review_note({}) == ""

    def test_none_decision_returns_empty(self):
        assert _human_review_note({"human_decision": None}) == ""

    def test_decision_without_comment_or_solution_returns_empty(self):
        state = {"human_decision": {"decision": "approve", "reviewer": "alice"}}
        assert _human_review_note(state) == ""

    def test_approved_with_comment_only(self):
        state = {
            "human_decision": {
                "decision": "approve",
                "reviewer": "alice",
                "comment": "Looks correct.",
            }
        }
        note = _human_review_note(state)
        assert "[Human Review - alice]" in note
        assert "Decision: Approved" in note
        assert "Comment: Looks correct." in note
        assert "Human Solution" not in note

    def test_rejected_with_comment_and_solution(self):
        state = {
            "human_decision": {
                "decision": "reject",
                "reviewer": "bob",
                "comment": "Missing root cause.",
            },
            "human_solution": "Escalate to L3 team.",
        }
        note = _human_review_note(state)
        assert "[Human Review - bob]" in note
        assert "Decision: Rejected" in note
        assert "Comment: Missing root cause." in note
        assert "Human Solution: Escalate to L3 team." in note

    def test_approved_with_solution_only_no_comment(self):
        state = {
            "human_decision": {
                "decision": "approve",
                "reviewer": "carol",
                "comment": "",
            },
            "human_solution": "Cleared the disk cache.",
        }
        note = _human_review_note(state)
        assert "Comment" not in note
        assert "Human Solution: Cleared the disk cache." in note

    def test_unknown_reviewer_fallback(self):
        state = {
            "human_decision": {"decision": "approve", "comment": "LGTM"},
        }
        note = _human_review_note(state)
        assert "[Human Review - unknown]" in note

    def test_whitespace_only_comment_is_ignored(self):
        state = {
            "human_decision": {
                "decision": "approve",
                "reviewer": "dave",
                "comment": "   ",
            }
        }
        assert _human_review_note(state) == ""

    def test_non_approve_decision_is_labelled_rejected(self):
        for verdict in ("reject", "REJECT", "denied", "no"):
            state = {
                "human_decision": {
                    "decision": verdict,
                    "reviewer": "eve",
                    "comment": "Bad fix.",
                }
            }
            note = _human_review_note(state)
            assert "Decision: Rejected" in note


# ---------------------------------------------------------------------------
# 2. ServiceNowClient.add_work_note() — write + verification logic
# ---------------------------------------------------------------------------

class TestAddWorkNote:

    @pytest.fixture
    def client(self):
        with patch("src.servicenow.auth.TokenManager.headers", return_value={}):
            from src.servicenow.client import ServiceNowClient
            return ServiceNowClient()

    def _patch_request(self, client, patch_response, check_response):
        """Patch client._request: first call = PATCH, second call = GET (verify)."""
        call_count = [0]
        def _side_effect(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return patch_response
            return check_response
        client._request = _side_effect

    def test_success_with_exact_match(self, client):
        note = "Server restarted by operator"
        self._patch_request(
            client,
            patch_response={"sys_id": "INC001"},
            check_response=[{"value": note}],
        )
        result = client.add_work_note("INC001", note)
        assert result == {"sys_id": "INC001"}

    def test_success_with_normalized_whitespace(self, client):
        """ServiceNow may collapse newlines — normalization must match."""
        note = "Line one\nLine two\nLine three"
        self._patch_request(
            client,
            patch_response={"sys_id": "INC001"},
            check_response=[{"value": "Line one Line two Line three"}],
        )
        result = client.add_work_note("INC001", note)
        assert result is not None

    def test_raises_when_journal_entry_is_empty(self, client):
        note = "Test note"
        self._patch_request(
            client,
            patch_response={"sys_id": "INC001"},
            check_response=[],
        )
        with pytest.raises(exc.ServiceNowWriteNotAppliedError, match="Work note not applied"):
            client.add_work_note("INC001", note)

    def test_raises_when_stored_value_does_not_match(self, client):
        note = "Expected note content"
        self._patch_request(
            client,
            patch_response={"sys_id": "INC001"},
            check_response=[{"value": "Something completely different"}],
        )
        with pytest.raises(exc.ServiceNowWriteNotAppliedError, match="Work note not applied"):
            client.add_work_note("INC001", note)

    def test_raises_on_empty_note_input(self, client):
        client._request = Mock()
        with pytest.raises(ValueError, match="Work note cannot be empty"):
            client.add_work_note("INC001", "")

    def test_raises_on_whitespace_only_note(self, client):
        client._request = Mock()
        with pytest.raises(ValueError, match="Work note cannot be empty"):
            client.add_work_note("INC001", "   \n\t  ")


# ---------------------------------------------------------------------------
# 3. act_node integration — write_work_note dispatched on human review
# ---------------------------------------------------------------------------

class TestActNodeWorkNoteIntegration:

    def _run_act(self, state, registry):
        """Run act_node with a patched TOOL_REGISTRY and approved permissions."""
        import src.agent.nodes.act as act_module
        original = act_module.TOOL_REGISTRY
        act_module.TOOL_REGISTRY = registry
        try:
            # Patch approvals so LOW_RISK_WRITE passes
            with patch("src.agent.tools.permissions.is_approved", return_value=True):
                return act_node(state)
        finally:
            act_module.TOOL_REGISTRY = original

    def test_write_work_note_called_on_approve_with_comment(self):
        state = _base_state(
            human_decision={"decision": "approve", "reviewer": "alice", "comment": "OK"},
        )
        write_note_mock = Mock(return_value={"sys_id": "note_1"})
        registry = ToolRegistry()
        registry.register("find_execution_log", PermissionClass.READ, Mock(return_value=None))
        registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE, Mock(return_value={}))
        registry.register("write_work_note", PermissionClass.LOW_RISK_WRITE, write_note_mock)
        registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE,
                          Mock(return_value={"sys_id": "log"}))

        with patch("src.agent.tools.permissions.is_approved", return_value=True):
            import src.agent.nodes.act as act_module
            original = act_module.TOOL_REGISTRY
            act_module.TOOL_REGISTRY = registry
            try:
                result = act_node(state)
            finally:
                act_module.TOOL_REGISTRY = original

        assert result["servicenow_write"] == "written"
        write_note_mock.assert_called_once()
        _, kwargs = write_note_mock.call_args
        note = kwargs["note"]
        assert "alice" in note
        assert "Approved" in note
        assert "OK" in note

    def test_write_work_note_called_on_reject_with_comment(self):
        state = _base_state(
            human_decision={"decision": "reject", "reviewer": "bob", "comment": "Needs more detail"},
        )
        write_note_mock = Mock(return_value={"sys_id": "note_2"})
        registry = ToolRegistry()
        registry.register("find_execution_log", PermissionClass.READ, Mock(return_value=None))
        registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE, Mock(return_value={}))
        registry.register("write_work_note", PermissionClass.LOW_RISK_WRITE, write_note_mock)
        registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE,
                          Mock(return_value={"sys_id": "log"}))

        with patch("src.agent.tools.permissions.is_approved", return_value=True):
            import src.agent.nodes.act as act_module
            original = act_module.TOOL_REGISTRY
            act_module.TOOL_REGISTRY = registry
            try:
                act_node(state)
            finally:
                act_module.TOOL_REGISTRY = original

        write_note_mock.assert_called_once()
        _, kwargs = write_note_mock.call_args
        note = kwargs["note"]
        assert "Rejected" in note
        assert "Needs more detail" in note

    def test_write_work_note_not_called_without_human_review(self):
        """Automatic resolution (no HITL) must never send a work note."""
        state = _base_state()  # no human_decision
        write_note_mock = Mock(return_value={})
        registry = ToolRegistry()
        registry.register("find_execution_log", PermissionClass.READ, Mock(return_value=None))
        registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE, Mock(return_value={}))
        registry.register("write_work_note", PermissionClass.LOW_RISK_WRITE, write_note_mock)
        registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE,
                          Mock(return_value={"sys_id": "log"}))

        with patch("src.agent.tools.permissions.is_approved", return_value=True):
            import src.agent.nodes.act as act_module
            original = act_module.TOOL_REGISTRY
            act_module.TOOL_REGISTRY = registry
            try:
                result = act_node(state)
            finally:
                act_module.TOOL_REGISTRY = original

        write_note_mock.assert_not_called()
        assert result["servicenow_write"] == "written"

    def test_write_work_note_verification_failure_is_warning_not_fatal(self):
        """ServiceNowWriteNotAppliedError from write_work_note must not crash act_node."""
        state = _base_state(
            human_decision={"decision": "approve", "reviewer": "carol", "comment": "LGTM"},
        )
        registry = ToolRegistry()
        registry.register("find_execution_log", PermissionClass.READ, Mock(return_value=None))
        registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE, Mock(return_value={}))
        registry.register("write_work_note", PermissionClass.LOW_RISK_WRITE,
                          Mock(side_effect=exc.ServiceNowWriteNotAppliedError(200, "Work note not applied")))
        registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE,
                          Mock(return_value={"sys_id": "log"}))

        with patch("src.agent.tools.permissions.is_approved", return_value=True):
            import src.agent.nodes.act as act_module
            original = act_module.TOOL_REGISTRY
            act_module.TOOL_REGISTRY = registry
            try:
                result = act_node(state)
            finally:
                act_module.TOOL_REGISTRY = original

        # Execution must complete normally despite the work note failure
        assert result["servicenow_write"] == "written"

    def test_idempotent_replay_skips_work_note(self):
        """If execution log already exists, act_node returns 'already_done' without writing note."""
        state = _base_state(
            human_decision={"decision": "approve", "reviewer": "dave", "comment": "OK"},
        )
        write_note_mock = Mock()
        registry = ToolRegistry()
        # Simulate execution log already present
        registry.register("find_execution_log", PermissionClass.READ,
                          Mock(return_value={"sys_id": "existing_log"}))
        registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE, Mock())
        registry.register("write_work_note", PermissionClass.LOW_RISK_WRITE, write_note_mock)
        registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE, Mock())

        with patch("src.agent.tools.permissions.is_approved", return_value=True):
            import src.agent.nodes.act as act_module
            original = act_module.TOOL_REGISTRY
            act_module.TOOL_REGISTRY = registry
            try:
                result = act_node(state)
            finally:
                act_module.TOOL_REGISTRY = original

        assert result["servicenow_write"] == "already_done"
        write_note_mock.assert_not_called()
