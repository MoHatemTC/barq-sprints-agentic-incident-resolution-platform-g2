import pytest
from unittest.mock import patch, MagicMock

from src.agent.nodes.determine_risk import determine_risk_node


def _mock_llm(content: str) -> MagicMock:
    mock = MagicMock()
    mock_response = MagicMock()
    mock_response.content = content
    mock.invoke.return_value = mock_response
    return mock


class TestDetermineRiskPriorityAnchors:
    """Tests covering the priority-anchored risk gate in determine_risk_node."""

    @pytest.mark.parametrize("priority", ["1", "2", 1, 2])
    @patch("src.agent.nodes.determine_risk.get_llm")
    def test_p1_and_p2_forced_to_high_without_calling_model(self, mock_get_llm, priority):
        """P1 and P2 incidents must always escalate to high risk without calling the LLM."""
        state = {
            "incident_payload": {
                "number": "INC001001",
                "priority": str(priority),
                "short_description": "Core switch offline",
                "description": "Entire network datacenter down",
            }
        }
        result = determine_risk_node(state)

        assert result == {"risk": "high"}
        mock_get_llm.assert_not_called()

    @pytest.mark.parametrize("priority", ["3", "4", "5"])
    @patch("src.agent.nodes.determine_risk.get_llm")
    def test_p3_to_p5_model_decides_low(self, mock_get_llm, priority):
        """P3-P5 with a model responding low should evaluate to low risk."""
        mock_llm = _mock_llm("low")
        mock_get_llm.return_value = mock_llm

        state = {
            "incident_payload": {
                "number": "INC001002",
                "priority": priority,
                "short_description": "Printer queue jammed",
                "description": "Office 4th floor printer not responding",
            }
        }
        result = determine_risk_node(state)

        assert result == {"risk": "low"}
        mock_get_llm.assert_called_once()
        # Verify the prompt instructs that P3-P5 is non-critical
        call_prompt = mock_llm.invoke.call_args[0][0]
        assert f"Priority P{priority} (non-critical)" in call_prompt

    @pytest.mark.parametrize("priority", ["3", "4", "5"])
    @pytest.mark.parametrize("llm_answer", ["high", "High.", "HIGH", "High risk: customer impact"])
    @patch("src.agent.nodes.determine_risk.get_llm")
    def test_p3_to_p5_model_escalates_to_high(self, mock_get_llm, priority, llm_answer):
        """For P3-P5, any model output containing 'high' (case-insensitive) escalates to high."""
        mock_llm = _mock_llm(llm_answer)
        mock_get_llm.return_value = mock_llm

        state = {
            "incident_payload": {
                "number": "INC001003",
                "priority": priority,
                "short_description": "Data leak suspected in staging logs",
                "description": "API key found in public log bundle",
            }
        }
        result = determine_risk_node(state)

        assert result == {"risk": "high"}
        mock_get_llm.assert_called_once()

    @patch("src.agent.nodes.determine_risk.get_llm")
    def test_no_priority_uses_generic_prompt_and_model_decides_high(self, mock_get_llm):
        """When priority is missing or empty, generic prompt is used and model determines high."""
        mock_llm = _mock_llm("high")
        mock_get_llm.return_value = mock_llm

        state = {
            "incident_payload": {
                "number": "INC001004",
                "short_description": "Authentication failure",
                "description": "All users locked out",
            }
        }
        result = determine_risk_node(state)

        assert result == {"risk": "high"}
        mock_get_llm.assert_called_once()
        call_prompt = mock_llm.invoke.call_args[0][0]
        assert "Priority P" not in call_prompt
        assert "significant business impact" in call_prompt

    @patch("src.agent.nodes.determine_risk.get_llm")
    def test_empty_string_priority_uses_generic_prompt_and_model_decides_low(self, mock_get_llm):
        """When priority is empty string, generic prompt is used and model determines low."""
        mock_llm = _mock_llm("low")
        mock_get_llm.return_value = mock_llm

        state = {
            "incident_payload": {
                "number": "INC001005",
                "priority": "",
                "short_description": "Minor glitch",
                "description": "Display font looks weird",
            }
        }
        result = determine_risk_node(state)

        assert result == {"risk": "low"}
        mock_get_llm.assert_called_once()
        call_prompt = mock_llm.invoke.call_args[0][0]
        assert "Priority P" not in call_prompt

    @patch("src.agent.nodes.determine_risk.get_llm")
    def test_approved_run_with_existing_risk_returns_empty_dict(self, mock_get_llm):
        """An already-approved run with existing risk set returns {} without invoking the model."""
        state = {
            "incident_payload": {
                "number": "INC001006",
                "priority": "1",
            },
            "human_decision": {
                "decision": "approve",
                "human_solution": "Applied hotfix",
            },
            "risk": "high",
        }
        result = determine_risk_node(state)

        assert result == {}
        mock_get_llm.assert_not_called()
