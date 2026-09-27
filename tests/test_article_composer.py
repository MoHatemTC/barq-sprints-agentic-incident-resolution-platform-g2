import json

import pytest

from src.agent import article_composer


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.last_prompt = None

    def invoke(self, prompt, **kwargs):
        self.last_prompt = prompt
        return self.response


def test_article_composer_accepts_grounded_minimal_input(monkeypatch):
    response = {
        "title": "Restart the affected service",
        "summary": "The human resolution is to restart the affected service.",
        "steps": [
            "Restart the affected service.",
            "Verify that the incident is resolved.",
        ],
    }

    fake_llm = FakeLLM(json.dumps(response))
    monkeypatch.setattr(
        article_composer,
        "get_llm",
        lambda: fake_llm,
    )

    incident_snapshot = {
        "number": "INC001001",
        "short_description": "Service unavailable",
        "description": "The service is unavailable.",
    }

    human_solution = "Restart the affected service."

    result = article_composer.compose_article(
        incident_snapshot,
        human_solution,
    )

    assert result["title"] == "Restart the affected service"
    assert len(result["steps"]) >= 1

    # The Composer prompt must contain both grounding sources.
    assert "Service unavailable" in fake_llm.last_prompt
    assert human_solution in fake_llm.last_prompt


def test_article_composer_rejects_empty_human_solution():
    with pytest.raises(ValueError, match="human_solution is required"):
        article_composer.compose_article(
            {
                "number": "INC001002",
                "short_description": "Service unavailable",
            },
            "",
        )


def test_article_composer_rejects_invalid_llm_json(monkeypatch):
    fake_llm = FakeLLM("This is not JSON")

    monkeypatch.setattr(
        article_composer,
        "get_llm",
        lambda: fake_llm,
    )

    with pytest.raises(
        ValueError,
        match="Article Composer returned invalid JSON",
    ):
        article_composer.compose_article(
            {
                "number": "INC001003",
                "short_description": "Service unavailable",
            },
            "Restart the service.",
        )


def test_article_composer_rejects_missing_steps(monkeypatch):
    response = {
        "title": "Restart service",
        "summary": "Restart the service.",
        "steps": [],
    }

    fake_llm = FakeLLM(json.dumps(response))

    monkeypatch.setattr(
        article_composer,
        "get_llm",
        lambda: fake_llm,
    )

    with pytest.raises(
        ValueError,
        match="at least one step",
    ):
        article_composer.compose_article(
            {
                "number": "INC001004",
                "short_description": "Service unavailable",
            },
            "Restart the service.",
        )


def test_article_composer_rejects_fabricated_details_on_sparse_input(
    monkeypatch,
):
    response = {
        "title": "Restart the service",
        "summary": (
            "The database server was unavailable because of a "
            "configuration problem."
        ),
        "steps": [
            "Restart the service.",
            "Check the database server configuration.",
        ],
    }

    fake_llm = FakeLLM(json.dumps(response))

    monkeypatch.setattr(
        article_composer,
        "get_llm",
        lambda: fake_llm,
    )

    # Deliberately sparse incident information.
    incident_snapshot = {
        "number": "INC-SPARSE-001",
        "short_description": "Service unavailable",
    }

    human_solution = "Restart the service."

    with pytest.raises(
        ValueError,
        match="unsupported technical details",
    ):
        article_composer.compose_article(
            incident_snapshot,
            human_solution,
        )

    prompt = fake_llm.last_prompt

    # Both grounding sources must be present.
    assert "Service unavailable" in prompt
    assert human_solution in prompt

    # The Composer prompt must explicitly prevent unsupported invention.
    assert (
        "Do not invent technical details, commands, causes, systems, "
        "or configuration."
    ) in prompt

    assert "do not guess it" in prompt.lower()

    # The sparse input itself must not contain the fabricated terms.
    assert "database" not in (
        json.dumps(incident_snapshot).lower()
        + human_solution.lower()
    )
    assert "server" not in (
        json.dumps(incident_snapshot).lower()
        + human_solution.lower()
    )


def test_article_composer_accepts_json_wrapped_in_code_fences(monkeypatch):
    """Real LLMs often answer with ```json fences (seen live on INC0010171)."""
    response = {
        "title": "Restart the email service",
        "summary": "Users could not access the email service; it was reported down.",
        "steps": ["Restart the email service.", "Verify that users can access email again."],
    }
    fence = "`" * 3
    fake_llm = FakeLLM(f"{fence}json\n{json.dumps(response, indent=2)}\n{fence}")
    monkeypatch.setattr(article_composer, "get_llm", lambda: fake_llm)

    article = article_composer.compose_article(
        {"number": "INC-FENCE-001", "short_description": "Email service unavailable"},
        "Restart the email service and verify that users can access email again.",
    )

    assert article["title"] == "Restart the email service"
    assert len(article["steps"]) == 2


def test_faithfulness_matches_whole_words_only(monkeypatch):
    """'reported' must not count as the technical term 'port'."""
    response = {
        "title": "Restore the payroll service",
        "summary": "The payroll service was reported down and support restarted it.",
        "steps": ["Restart the payroll service."],
    }
    monkeypatch.setattr(article_composer, "get_llm", lambda: FakeLLM(json.dumps(response)))

    article = article_composer.compose_article(
        {"number": "INC-WORD-001", "short_description": "Payroll service down"},
        "Restart the payroll service.",
    )

    assert article["title"] == "Restore the payroll service"

