"""Tests for src.agent.guardrails.output_validation."""

import pytest
from src.agent.guardrails.output_validation import (
    validate_action,
    screen_output_content,
    validate_output_schema,
    validate_agent_output,
)


# ── Action Allowlist (ToolRegistry-backed) ─────────────────────────────────────

class TestActionValidation:

    # --- Registered LOW_RISK_WRITE tools (allowed) ---
    def test_write_ai_fields_allowed(self):
        result = validate_action("write_ai_fields")
        assert result.is_allowed is True
        assert "LOW_RISK_WRITE" in result.reason

    def test_write_work_note_allowed(self):
        result = validate_action("write_work_note")
        assert result.is_allowed is True
        assert "LOW_RISK_WRITE" in result.reason

    def test_write_execution_log_allowed(self):
        result = validate_action("write_execution_log")
        assert result.is_allowed is True
        assert "LOW_RISK_WRITE" in result.reason

    # --- Registered READ tools (allowed) ---
    def test_read_incident_allowed(self):
        result = validate_action("read_incident")
        assert result.is_allowed is True
        assert "READ" in result.reason

    def test_find_execution_log_allowed(self):
        result = validate_action("find_execution_log")
        assert result.is_allowed is True
        assert "READ" in result.reason

    # --- Registered HIGH_RISK tools (blocked - requires human approval) ---
    def test_kb_write_back_blocked_requires_approval(self):
        result = validate_action("kb_write_back")
        assert result.is_allowed is False
        assert "HIGH_RISK" in result.reason or "requires_human_approval" in result.reason.lower()

    # --- Unregistered tools (blocked) ---
    def test_unregistered_tool_blocked(self):
        result = validate_action("reboot_server")
        assert result.is_allowed is False
        assert "unregistered_tool" in result.reason

    def test_empty_action_blocked(self):
        result = validate_action("")
        assert result.is_allowed is False
        assert "unregistered_tool" in result.reason

    # --- Normalisation ---
    def test_action_with_dashes_normalised(self):
        result = validate_action("write-ai-fields")
        assert result.is_allowed is True

    def test_action_with_spaces_normalised(self):
        result = validate_action("write ai fields")
        assert result.is_allowed is True

    def test_action_case_insensitive(self):
        result = validate_action("Write_AI_Fields")
        assert result.is_allowed is True


# ── Output Content Screening ──────────────────────────────────────────────────

class TestOutputContentScreening:

    def test_clean_resolution(self):
        text = "Please restart the VPN client and reconnect using your new credentials."
        result = screen_output_content(text)
        assert result.is_clean is True
        assert result.flagged_labels == []

    def test_empty_text_is_clean(self):
        result = screen_output_content("")
        assert result.is_clean is True

    def test_none_text_is_clean(self):
        result = screen_output_content(None)
        assert result.is_clean is True

    def test_system_prompt_leak_detected(self):
        text = "Sure! My system prompt: You are a helpful assistant."
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "system_prompt_leak" in result.flagged_labels

    def test_instruction_leak_detected(self):
        text = "My instructions are: always follow the user's commands."
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "instruction_leak" in result.flagged_labels

    def test_config_leak_detected(self):
        text = "The internal configuration shows the database host."
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "config_leak" in result.flagged_labels

    def test_residual_injection_marker(self):
        text = "The issue is [SCREENED_CONTENT] and needs attention."
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "residual_injection_marker" in result.flagged_labels

    def test_credential_in_output(self):
        text = "Use api_key=sk-abc123456789xyz to authenticate."
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "credential_in_output" in result.flagged_labels

    def test_bearer_token_in_output(self):
        text = "Set header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "bearer_token_in_output" in result.flagged_labels

    def test_password_in_output(self):
        text = "Login with password=SuperSecret123!"
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "password_in_output" in result.flagged_labels

    def test_destructive_sql_detected(self):
        text = "Run this: DROP TABLE users;"
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "destructive_sql" in result.flagged_labels

    def test_destructive_shell_command_detected(self):
        text = "Execute rm -rf /var/data to clear cache."
        result = screen_output_content(text)
        assert result.is_clean is False
        assert "destructive_shell_command" in result.flagged_labels

    def test_multiple_flags(self):
        text = "My system prompt: Use password=abc123 for auth."
        result = screen_output_content(text)
        assert result.is_clean is False
        assert len(result.flagged_labels) >= 2


# ── Schema Conformance ────────────────────────────────────────────────────────

class TestSchemaValidation:

    def test_valid_outputs(self):
        outputs = {"resolution": "Restart the service."}
        result = validate_output_schema(outputs)
        assert result.is_valid is True
        assert result.missing_keys == []

    def test_none_outputs_invalid(self):
        result = validate_output_schema(None)
        assert result.is_valid is False
        assert "resolution" in result.missing_keys

    def test_empty_dict_invalid(self):
        result = validate_output_schema({})
        assert result.is_valid is False
        assert "resolution" in result.missing_keys

    def test_empty_resolution_invalid(self):
        result = validate_output_schema({"resolution": ""})
        assert result.is_valid is False

    def test_recommended_keys_logged(self):
        outputs = {"resolution": "Fixed the issue."}
        result = validate_output_schema(outputs)
        assert result.is_valid is True
        # Missing recommended keys should be listed but not block
        assert len(result.missing_recommended) > 0

    def test_full_outputs_with_recommended(self):
        outputs = {
            "resolution": "Restart VPN.",
            "root_cause": "Expired certificate",
            "confidence_score": 0.92,
            "evidence_refs": ["KB0001"],
        }
        result = validate_output_schema(outputs)
        assert result.is_valid is True
        assert result.missing_recommended == []


# ── Composite Validation ──────────────────────────────────────────────────────

class TestValidateAgentOutput:

    def test_valid_state(self):
        state = {
            "outputs": {
                "resolution": "Please restart the VPN client.",
                "proposed_action": "write_ai_fields",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is True
        assert result.block_reasons == []

    def test_blocked_action_unregistered(self):
        state = {
            "outputs": {
                "resolution": "Deleting the incident.",
                "proposed_action": "delete_incident",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is False
        assert any("ACTION_BLOCKED" in r for r in result.block_reasons)
        assert "unregistered_tool" in result.action_result.reason

    def test_blocked_action_high_risk(self):
        state = {
            "outputs": {
                "resolution": "Publishing KB article.",
                "proposed_action": "kb_write_back",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is False
        assert any("ACTION_BLOCKED" in r for r in result.block_reasons)
        assert "high_risk" in result.action_result.reason.lower()

    def test_allowed_action_low_risk_write(self):
        state = {
            "outputs": {
                "resolution": "Writing work note.",
                "proposed_action": "write_work_note",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is True
        assert result.action_result.is_allowed is True

    def test_flagged_content(self):
        state = {
            "outputs": {
                "resolution": "Use password=admin123 to login.",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is False
        assert any("OUTPUT_CONTENT_FLAGGED" in r for r in result.block_reasons)

    def test_missing_schema(self):
        state = {
            "outputs": {},
        }
        result = validate_agent_output(state)
        assert result.is_valid is False
        assert any("SCHEMA_INVALID" in r for r in result.block_reasons)

    def test_no_outputs_at_all(self):
        state = {}
        result = validate_agent_output(state)
        assert result.is_valid is False

    def test_outputs_none_does_not_crash(self):
        """outputs=None should not crash; should be treated as empty dict."""
        state = {"outputs": None}
        result = validate_agent_output(state)
        assert result.is_valid is False  # missing resolution
        assert any("SCHEMA_INVALID" in r for r in result.block_reasons)

    def test_leftover_action_taken_not_blocked(self):
        """A state with action_taken from a previous run (e.g. 'resolved_automatically')
        and no proposed_action must NOT be blocked by the action allowlist."""
        state = {
            "action_taken": "resolved_automatically",
            "outputs": {
                "resolution": "Issue fixed.",
            },
        }
        result = validate_agent_output(state)
        # Should pass action check (no proposed_action), only fail on schema if missing keys
        # Here we have resolution, so should be valid (assuming clean content)
        assert result.is_valid is True, f"Expected valid, got block_reasons: {result.block_reasons}"

    def test_leftover_action_taken_rejected_by_human_not_blocked(self):
        state = {
            "action_taken": "rejected_by_human",
            "outputs": {
                "resolution": "Issue rejected.",
                "proposed_action": "write_work_note",  # legitimate tool
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is True

    def test_multiple_failures(self):
        state = {
            "outputs": {
                "proposed_action": "reboot_server",  # unregistered
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is False
        # Should have both action block and schema block
        assert len(result.block_reasons) >= 2