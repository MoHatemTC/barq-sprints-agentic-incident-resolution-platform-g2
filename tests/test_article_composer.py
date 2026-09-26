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
def test_article_composer_prompt_enforces_faithfulness_on_sparse_input(
    monkeypatch,
):
    response = {
        "title": "Restart the service",
        "summary": "The service was unavailable.",
        "steps": [
            "Restart the service.",
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

    result = article_composer.compose_article(
        incident_snapshot,
        human_solution,
    )

    assert result["title"] == "Restart the service"
    assert result["steps"] == ["Restart the service."]

    prompt = fake_llm.last_prompt

    # Both grounding sources must be present.
    assert "Service unavailable" in prompt
    assert human_solution in prompt

    # The prompt must explicitly prevent unsupported technical invention.
    assert (
        "Do not invent technical details, commands, causes, systems, "
        "or configuration."
    ) in prompt

    assert (
        "do not guess it"
    ) in prompt.lower()

    # Sparse input must not introduce technical facts that were not supplied.
    assert "database" not in prompt.lower()
    assert "server" not in prompt.lower()
    assert "configuration" in prompt.lower()