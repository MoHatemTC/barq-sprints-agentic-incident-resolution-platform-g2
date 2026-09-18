"""
Track B: turn the BARQ IT Service Desk Manual (PDF) into sections whose ids the
evaluation dataset uses: "3.4", "Appendix B.1", "Document control", "6.13 KB0010 v1".

  python -m src.retrieval.manual_parser                                   # list sections
  python -m src.retrieval.manual_parser --check eval/barq_rag_eval_dataset.json
"""

import argparse
import json
import re
from dataclasses import dataclass

import pymupdf

from ..config import RETRIEVAL

# Repeated on every page -> removed (the dataset forbids "header_footer_noise").
NOISE = {"BARQ Systems – IT Service Operations Manual", "INTERNAL DOCUMENT", "Edition 4.0"}
PAGE_NO = re.compile(r"^\d+ of \d+")
# Table-of-contents line: "3.4 Response and resolution targets ........ 11"
TOC = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?|Appendix [A-E]|Document control)\.?\s*([^.]*?)\s*\.{3,}\s*\d+$")
SUB_APPENDIX = re.compile(r"^([A-E])\.(\d) \S")                   # "B.1 Escalation handover"
KB_REVISION = re.compile(r"^Version (\d) – (retired|published) ", re.M)  # "Version 1 – retired 02 April 2026"


@dataclass
class ManualSection:
    section_id: str            # id the dataset cites
    section_label: str         # id + KB number + version when it is a KB article
    title: str
    text: str
    page_start: int
    kb_number: str = ""
    category: str = ""         # chapter number / "appendix" / "front-matter"
    service: str = ""
    workflow_state: str = "published"
    security_level: str = "internal"   # Document control: classification Internal
    version: int = 4                   # manual edition unless a KB header says otherwise


def read_pages(pdf_path: str) -> list[list[str]]:
    """Clean lines per page: no blanks, no running header/footer, one dash kind."""
    pages = []
    for page in pymupdf.open(pdf_path):
        lines = [l.replace("—", "–").strip() for l in page.get_text().splitlines()]
        pages.append([l for l in lines if l and l not in NOISE and not PAGE_NO.match(l)])
    return pages


def read_toc(pages: list[list[str]]) -> dict[str, str]:
    """{id: title} from the contents pages (2-4). The TOC decides what a heading is."""
    toc = {}
    for line in pages[1] + pages[2] + pages[3]:
        m = TOC.match(line)
        if m:
            toc[m.group(1)] = m.group(2).strip(" –")
    return toc


def heading_id(line: str, toc: dict[str, str], appendix: str) -> str | None:
    """Section id if `line` is a heading, else None."""
    for sid, title in sorted(toc.items(), key=lambda kv: -len(kv[0])):   # "12.2" before "12"
        if line.startswith(sid) and title in line:
            return sid
    m = SUB_APPENDIX.match(line)                  # "B.1 ..." only counts inside Appendix B
    return f"Appendix {m.group(1)}.{m.group(2)}" if m and m.group(1) == appendix else None


def split_sections(pages: list[list[str]], toc: dict[str, str]) -> list[ManualSection]:
    """Walk the body (page 5 onward); every known heading starts a new section."""
    sections, appendix = [], ""
    for page_no, lines in enumerate(pages[4:], start=5):
        for line in lines:
            sid = heading_id(line, toc, appendix)
            if sid:
                appendix = sid[-1] if re.fullmatch(r"Appendix [A-E]", sid) else appendix
                sections.append(ManualSection(sid, sid, toc.get(sid, line), "", page_no))
            elif sections:
                sections[-1].text += line + "\n"
    return [s for s in sections if s.text.strip()]   # chapter headings like "3" hold no text of their own


def value_after(text: str, key: str) -> str:
    """KB header blocks are 'key' on one line, value on the next."""
    m = re.search(rf"^{key}\n(.+)$", text, re.M)
    return m.group(1) if m else ""


def tag_kb(s: ManualSection) -> list[ManualSection]:
    """Chapter-6 articles: read state/version/service. 6.13 holds two revisions -> two sections."""
    kb = re.search(r"KB\d{4}", s.title)
    if not kb:
        return [s]
    s.kb_number = kb.group()
    revisions = list(KB_REVISION.finditer(s.text)) if "\nVersion 1 –" in s.text else []
    if not revisions:
        s.workflow_state = (value_after(s.text, "State") or s.workflow_state).lower()
        s.version = int(value_after(s.text, "Version") or s.version)
        s.service = value_after(s.text, "Service")
        s.section_label = f"{s.section_id} {s.kb_number} v{s.version}"
        return [s]
    parts = []
    for i, m in enumerate(revisions):
        end = revisions[i + 1].start() if i + 1 < len(revisions) else len(s.text)
        body = s.text[m.start():end]
        parts.append(ManualSection(
            s.section_id, f"{s.section_id} {s.kb_number} v{m.group(1)}", s.title, body, s.page_start,
            kb_number=s.kb_number, category=s.category, service=value_after(body, "Service") or "order-processing",
            workflow_state=m.group(2), version=int(m.group(1)),
        ))
    return parts


def parse_manual(pdf_path: str | None = None) -> list[ManualSection]:
    pages = read_pages(pdf_path or RETRIEVAL.manual_pdf_path)
    out = []
    for s in split_sections(pages, read_toc(pages)):
        s.category = ("front-matter" if s.section_id == "Document control"
                      else "appendix" if s.section_id.startswith("Appendix") else "chapter-" + s.section_id.split(".")[0])
        if s.section_id == "6.3":                     # "the archived scan": audit copy, not current text
            s.workflow_state = "archived"
        out += tag_kb(s)
    return out


def ids_for(section_id: str, section_label: str) -> set[str]:
    """Ids a chunk satisfies: its id, its label, and its parent appendix ("Appendix B.1" -> "Appendix B")."""
    ids = {section_id, section_label}
    if re.fullmatch(r"Appendix [A-E]\.\d", section_id):
        ids.add(section_id[:10])
    return ids


def check_against_dataset(sections: list[ManualSection], dataset_path: str) -> list[str]:
    """Ids the dataset expects or forbids that the parser did not produce."""
    wanted = set()
    for session in json.load(open(dataset_path, encoding="utf-8"))["sessions"]:
        for t in session["turns"]:
            wanted |= set(t["expected_sections"]) | set(t["must_not_retrieve"])
    have = set().union(*(ids_for(s.section_id, s.section_label) for s in sections))
    return sorted(wanted - have - {"—", "header_footer_noise"})   # not sections: unanswerable marker, stripped noise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", help="path to barq_rag_eval_dataset.json")
    args = ap.parse_args()
    sections = parse_manual()
    for s in sections:
        print(f"p{s.page_start:<3} {s.section_label:20s} {s.workflow_state:9s} v{s.version} {len(s.text):5d} chars  {s.title[:45]}")
    print(f"\n{len(sections)} sections")
    if args.check:
        print("missing ids:", check_against_dataset(sections, args.check) or "none")
