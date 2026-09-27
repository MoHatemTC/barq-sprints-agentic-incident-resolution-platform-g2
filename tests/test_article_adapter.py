from src.agent.article_adapter import composer_result_to_article


def test_composer_result_is_converted_to_article():
    composed = {
        "title": "Restart the affected service",
        "summary": "The service was unavailable.",
        "steps": [
            "Restart the affected service.",
            "Verify that the incident is resolved.",
        ],
    }

    article = composer_result_to_article(
        composed=composed,
        article_number="KB00999",
        category="network",
        service="Email",
        security_level="internal",
    )

    assert article.number == "KB00999"
    assert article.article_id == "KB00999"
    assert article.title == "Restart the affected service"

    assert "The service was unavailable." in article.body
    assert "1. Restart the affected service." in article.body
    assert "2. Verify that the incident is resolved." in article.body

    assert article.category == "network"
    assert article.service == "Email"
    assert article.workflow_state == "published"
    assert article.version == 1
    assert article.security_level == "internal"
    assert article.section == "Human Resolution"