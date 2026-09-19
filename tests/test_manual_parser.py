# unit tests for the manual parser on small hand-made pages. No PDF, no models
# Run: pytest tests/test_manual_parser.py -v
from src.retrieval.manual_parser import ManualSection, heading_id, ids_for, read_toc, split_sections, tag_kb

# A miniature manual: page 1 cover, pages 2-4 contents, page 5+ body (the parser skips pages 1-4)
TOC_PAGE = [
    "Document control ............ 5",
    "3. Incident management ............ 10",
    "3.4 Response and resolution targets ............ 11",
    "6.13 KB0010 – Order service connection pool exhaustion ............ 23",
    "12. Reporting ............ 44",
    "12.2 Reporting to service delivery managers ............ 44",
    "Appendix B – Templates ............ 47",
]
BODY = [
    "Document control", "This manual is a controlled document.",
    "3. Incident management",                       # chapter heading with no text of its own
    "3.4 Response and resolution targets", "P1 is 15 minutes.", "3. Not a heading, just a numbered step.",
    "12.2 Reporting to service delivery managers", "Monthly.",
    "Appendix B – Templates",
    "B.1 Escalation handover", "Paste into the work note.",
]
PAGES = [["cover"], TOC_PAGE, [], [], BODY]


def test_toc_maps_ids_to_titles():
    toc = read_toc(PAGES)
    assert toc["3.4"] == "Response and resolution targets"
    assert toc["Appendix B"] == "Templates"
    assert toc["Document control"] == ""


def test_longer_id_wins_over_chapter_prefix():
    toc = read_toc(PAGES)
    assert heading_id("12.2 Reporting to service delivery managers", toc, "") == "12.2"
    assert heading_id("3. Not a heading, just a numbered step.", toc, "") is None


def test_split_sections_uses_toc_and_drops_empty_chapters():
    sections = split_sections(PAGES, read_toc(PAGES))
    ids = [s.section_id for s in sections]
    assert ids == ["Document control", "3.4", "12.2", "Appendix B.1"]     # "3" and "Appendix B" hold no text
    assert sections[1].text.strip() == "P1 is 15 minutes.\n3. Not a heading, just a numbered step."
    assert sections[3].page_start == 5


def test_kb_article_reads_state_and_version_from_header_block():
    s = ManualSection("6.4", "6.4", "KB0001 – VPN authentication fails", "KB0001\nState\nPublished\nVersion\n2\nService\ncorporate-vpn\n", 18)
    (out,) = tag_kb(s)
    assert (out.section_label, out.workflow_state, out.version, out.service) == ("6.4 KB0001 v2", "published", 2, "corporate-vpn")


def test_two_revisions_become_two_sections_with_their_own_state():
    text = ("Both revisions are reproduced.\n"
            "Version 1 – retired 02 April 2026\nKB0010\nState\nRetired\nRestart the server.\n"
            "Version 2 – published 02 April 2026\nKB0010\nState\nPublished\nService\norder-processing\nDo not restart.\n")
    v1, v2 = tag_kb(ManualSection("6.13", "6.13", "KB0010 – Order service", text, 23))
    assert (v1.section_label, v1.workflow_state, v1.version) == ("6.13 KB0010 v1", "retired", 1)
    assert (v2.section_label, v2.workflow_state, v2.version) == ("6.13 KB0010 v2", "published", 2)
    assert "Restart the server." in v1.text and "Restart the server." not in v2.text


def test_ids_for_includes_label_and_parent_appendix():
    assert ids_for("6.13", "6.13 KB0010 v1") == {"6.13", "6.13 KB0010 v1"}
    assert ids_for("Appendix B.1", "Appendix B.1") == {"Appendix B.1", "Appendix B"}
