from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString


def strip_article_html(content: str) -> str:
    """Convert article HTML to readable Markdown while preserving code."""
    if not content:
        return ""

    # Avoid parsing content that is already plain text or Markdown.
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
    # One table row per line, cells separated: "Number | INC0010047", not "NumberINC0010047".
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        row.replace_with(NavigableString("\n" + " | ".join(cells) + "\n"))

    # List items and paragraphs each start on their own line.
    for ol in soup.find_all("ol"):
        for n, li in enumerate(ol.find_all("li", recursive=False), start=1):
            li.insert_before(NavigableString(f"\n{n}. "))
    for li in soup.find_all("li"):
        if li.parent is None or li.parent.name != "ol":
            li.insert_before(NavigableString("\n- "))
    for block in soup.find_all(["p", "div"]):
        block.insert_before(NavigableString("\n"))
        block.insert_after(NavigableString("\n"))

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
