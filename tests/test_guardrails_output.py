"""Tests for src.agent.guardrails.output_validation."""

import pytest
from src.agent.guardrails.output_validation import (
    validate_action,
    screen_output_content,
    validate_output_schema,
    validate_agent_output,
    ALLOWED_ACTIONS,
    DENIED_ACTIONS,
)


# ── Action Allowlist ──────────────────────────────────────────────────────────

class TestActionValidation:

    # --- Allowed actions ---
    def test_update_incident_allowed(self):
        result = validate_action("update_incident")
        assert result.is_allowed is True

    def test_add_comment_allowed(self):
        result = validate_action("add_comment")
        assert result.is_allowed is True

    def test_resolve_incident_allowed(self):
        result = validate_action("resolve_incident")
        assert result.is_allowed is True

    def test_reassign_incident_allowed(self):
        result = validate_action("reassign_incident")
        assert result.is_allowed is True

    def test_escalate_incident_allowed(self):
        result = validate_action("escalate_incident")
        assert result.is_allowed is True

    # --- Explicitly denied actions ---
    def test_delete_incident_denied(self):
        result = validate_action("delete_incident")
        assert result.is_allowed is False
        assert "denied" in result.reason.lower()

    def test_drop_table_denied(self):
        result = validate_action("drop_table")
        assert result.is_allowed is False

    def test_execute_script_denied(self):
        result = validate_action("execute_script")
        assert result.is_allowed is False

    def test_run_command_denied(self):
        result = validate_action("run_command")
        assert result.is_allowed is False

    def test_create_admin_denied(self):
        result = validate_action("create_admin")
        assert result.is_allowed is False

    # --- Unknown actions (not on allowlist) ---
    def test_unknown_action_blocked(self):
        result = validate_action("reboot_server")
        assert result.is_allowed is False
        assert "allowlist" in result.reason.lower()

    def test_empty_action_blocked(self):
        result = validate_action("")
        assert result.is_allowed is False

    # --- Normalisation ---
    def test_action_with_dashes_normalised(self):
        result = validate_action("update-incident")
        assert result.is_allowed is True

    def test_action_with_spaces_normalised(self):
        result = validate_action("update incident")
        assert result.is_allowed is True

    def test_action_case_insensitive(self):
        result = validate_action("Update_Incident")
        assert result.is_allowed is True


# ── Output Content Screening ─────────────────────────────────────────────────

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
                "proposed_action": "update_incident",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is True
        assert result.block_reasons == []

    def test_blocked_action(self):
        state = {
            "outputs": {
                "resolution": "Deleting the incident.",
                "proposed_action": "delete_incident",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is False
        assert any("ACTION_BLOCKED" in r for r in result.block_reasons)

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

    def test_multiple_failures(self):
        state = {
            "outputs": {
                "proposed_action": "drop_table",
            },
        }
        result = validate_agent_output(state)
        assert result.is_valid is False
        # Should have both action block and schema block
        assert len(result.block_reasons) >= 2
