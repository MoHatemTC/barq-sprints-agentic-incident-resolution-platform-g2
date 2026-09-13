from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag


def strip_article_html(content: str) -> str:
    """Convert article HTML to readable Markdown while preserving code."""
    if not content:
        return ""

    # Already-Markdown/plain-text content should remain unchanged.
    if "<" not in content or ">" not in content:
        return content.strip()

    soup = BeautifulSoup(content, "html.parser")

    # Preserve fenced code blocks before removing presentation tags.
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        code_text = code.get_text() if code else pre.get_text()
        replacement = NavigableString(f"\n```\n{code_text.rstrip()}\n```\n")
        pre.replace_with(replacement)

    # Preserve inline code as Markdown inline code.
    for code in soup.find_all("code"):
        code_text = code.get_text()
        code.replace_with(NavigableString(f"`{code_text}`"))

    # Convert common headings to Markdown headings.
    for level in range(1, 7):
        for heading in soup.find_all(f"h{level}"):
            heading.replace_with(
                NavigableString(
                    f"\n{'#' * level} {heading.get_text(' ', strip=True)}\n"
                )
            )

    # Preserve line breaks.
    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))

    # Remove presentation-only HTML by extracting its text. No artificial
    # separator: inserting one (e.g. " ") between every node boundary adds
    # spurious whitespace where the source had none -- e.g. "<code>x</code>."
    # would otherwise become "`x` ." with a stray space before the period.
    # All real spacing/newlines are already preserved via the original text
    # nodes plus the explicit <br>/heading replacements above.
    text = soup.get_text("", strip=False)

    # Normalize whitespace without changing technical tokens.
    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)

    result = "\n\n".join(lines)

    # Clean up spacing around Markdown code fences.
    result = result.replace("```\n\n", "```\n")
    result = result.replace("\n\n```", "\n```")

    return result.strip()