from src.retrieval.chunking import chunk_article
from src.retrieval.preprocessing import strip_article_html


def test_strip_article_html_removes_presentation_tags_and_keeps_text_readable():
    html = (
        "<div><h2>Symptom</h2><p>SAP GUI returns "
        "<strong>RFC_ERROR_COMMUNICATION</strong><br>"
        "Path: <span>C:\\Program Files\\SAP</span></p></div>"
    )

    text = strip_article_html(html)

    assert "<div>" not in text
    assert "<strong>" not in text
    assert "## Symptom" in text
    assert "SAP GUI returns RFC_ERROR_COMMUNICATION" in text
    assert "C:\\Program Files\\SAP" in text


def test_strip_article_html_preserves_inline_code_and_technical_tokens():
    html = (
        "<p>Check <code>/message-server</code> when the gateway reports "
        "<code>HTTP 502</code> from <code>APPSRV-OLD-04</code>.</p>"
    )

    text = strip_article_html(html)

    assert "`/message-server`" in text
    assert "`HTTP 502`" in text
    assert "`APPSRV-OLD-04`" in text

    assert "/message-server" in text
    assert "HTTP 502" in text
    assert "APPSRV-OLD-04" in text


def test_strip_article_html_preserves_pre_code_blocks_without_altering_commands():
    html = (
        "<h2>Resolution</h2><p>Run the diagnostic command:</p>"
        "<pre><code>curl -i https://sap.example.com/message-server\n"
        "Get-Content \"C:\\Program Files\\SAP\\trace.log\"</code></pre>"
    )

    text = strip_article_html(html)

    assert "```" in text
    assert "curl -i https://sap.example.com/message-server" in text
    assert 'Get-Content "C:\\Program Files\\SAP\\trace.log"' in text
    assert "<pre>" not in text
    assert "<code>" not in text


def test_markdown_code_fences_and_inline_code_survive_unchanged():
    markdown = (
        "## Resolution\n"
        "Run `sapcontrol -nr 00 -function GetProcessList`.\n\n"
        "```\n"
        "tail -f /var/log/sap/gateway.log\n"
        "RFC_ERROR_COMMUNICATION\n"
        "```\n"
    )

    assert strip_article_html(markdown) == markdown.strip()


def test_chunk_article_strips_html_before_section_splitting():
    html = (
        "<h2>Symptom</h2><p>Gateway reports <code>HTTP 502</code>.</p>"
        "<h2>Resolution</h2><p>Replace APPSRV-OLD-04 with /message-server.</p>"
    )

    chunks = chunk_article(html)

    assert chunks == [
        ("Symptom", "## Symptom\n\nGateway reports `HTTP 502`."),
        (
            "Resolution",
            "## Resolution\n\nReplace APPSRV-OLD-04 with /message-server.",
        ),
    ]