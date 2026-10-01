"""
Rewrite the manual's raw PDF text into readable article HTML for ServiceNow
  python tools/format_manual.py
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.agent.llm import get_llm  # noqa: E402
from src.retrieval.manual_parser import parse_manual  # noqa: E402

OUT = REPO / "data" / "manual_formatted.json"
FOOTER = "BARQ Systems · IT Service Operations Manual"
FACT = re.compile(r"[A-Za-z]*\d[\w:./%–-]*")      # KB0004, INC0010047, P1, 15, 24/7, 11:32
WORD = re.compile(r"[a-z]{4,}")

PROMPT = """You format one section of an IT service desk manual as HTML for a ServiceNow knowledge article.
The text below was extracted from a PDF: every table cell is on its own line, sentences are broken
across lines, and tables are flattened into one value per line.

Rebuild its layout. Rules:
- Keep every word, number, name, time and identifier exactly as written. Do not summarise, reword,
  explain, translate or add anything. Your job is layout only.
- Join broken lines back into sentences and paragraphs (<p>).
- A run of label/value lines (e.g. "State", "Published", "Version", "2") becomes a two-column
  <table> of label and value.
- An upper-case header row followed by values becomes a <table> with <th> headers and one <tr> per row.
- "Symptom.", "Cause.", "Resolution.", "Escalation." and similar lead-ins become <h2> headings.
- Numbered steps become <ol><li>, and "▪" bullets become <ul><li>.
- Allowed tags only: h2 h3 p ul ol li table thead tbody tr th td strong em code br. No attributes,
  no CSS, no <html>/<body>, no title heading (the title is stored separately).
- Output the HTML only, without code fences.

Section: {title}

Text:
{text}
"""


def _source(section) -> str:
    lines = [l for l in section.text.splitlines() if l.strip() != FOOTER]
    if section.kb_number and lines and lines[0].strip() == section.kb_number:
        lines = lines[2:]   # "KB0004" / "PRINT JOBS QUEUE…": repeats the article title
    return "\n".join(lines).strip()


def _plain(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def check(source: str, result: str) -> list[str]:
    """Why `result` is not a faithful layout of `source` (empty list = faithful)."""
    # Step numbers ("3. Stop the spooler") become <ol> items, so they are not facts to find.
    src_plain = re.sub(r"(?m)^\d{1,2}\.\s", "", html.unescape(source))
    out_plain = _plain(result)
    out_compact = re.sub(r"\s+", "", out_plain)
    problems = []
    lost_facts = sorted({f for f in FACT.findall(src_plain) if f.rstrip(".,–-") not in out_compact})
    if lost_facts:
        problems.append(f"facts missing: {lost_facts[:10]}")
    src_words, out_words = set(WORD.findall(src_plain.lower())), WORD.findall(out_plain.lower())
    kept = len(src_words & set(out_words)) / max(len(src_words), 1)
    if kept < 0.97:
        problems.append(f"only {kept:.0%} of source words kept: {sorted(src_words - set(out_words))[:10]}")
    new = [w for w in out_words if w not in src_words]
    if len(new) > 0.03 * max(len(out_words), 1):
        problems.append(f"{len(new)} added words: {sorted(set(new))[:10]}")
    return problems


def format_section(section) -> tuple[str, dict]:
    source = _source(section)
    digest = hashlib.sha256(source.encode()).hexdigest()
    problems = []
    for _ in range(2):   # one retry: a fresh answer usually fixes a dropped cell
        reply = get_llm().invoke(PROMPT.format(title=section.title, text=source))
        result = getattr(reply, "content", reply).strip()
        result = re.sub(r"^```(?:html)?\s*|\s*```$", "", result)
        problems = check(source, result)
        if not problems:
            return section.section_label, {"source_sha256": digest, "html": result}
    return section.section_label, {"source_sha256": digest, "html": None, "problems": problems}


def main() -> None:
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    sections = [s for s in parse_manual() if s.workflow_state == "published"]
    todo = [s for s in sections
            if not (done.get(s.section_label, {}).get("html")
                    and done[s.section_label]["source_sha256"]
                    == hashlib.sha256(_source(s).encode()).hexdigest())]
    print(f"{len(sections) - len(todo)} up to date, formatting {len(todo)}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        for label, entry in pool.map(format_section, todo):
            done[label] = entry
            print(("ok     " if entry["html"] else "FAILED ") + label
                  + ("" if entry["html"] else f"  {entry['problems']}"))
            OUT.write_text(json.dumps(done, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    failed = [label for label, e in done.items() if not e.get("html")]
    print(f"\n{len(done) - len(failed)}/{len(sections)} formatted" + (f"; failed: {failed}" if failed else ""))


if __name__ == "__main__":
    main()
