"""
Build the versioned evaluation set from data/coverage_matrix.csv -> eval/evaluation_set.json
The JSON is committed so the exam paper is frozen and versioned even if the CSV changes later.
to generate version Run:  python eval/build_evaluation_set.py

v2.0 grades against the manual-only KB: articles that left with the Sprint 1 corpus
are dropped from the expected answers (see retarget), and the manual's own "reported as"
phrases (6.1) are added. v1.x items keep their ids; a changed answer is kept in
``v1_expected_articles`` so the change is visible.

v1.2 adds the manual's stressor rows.  They are graded on a different axis from
the KB incidents -- a KB incident is answered by a KB number, a manual stressor by
a manual section -- so they live in the same set under their own source and the
harness grades them separately.  The v1.1 items are emitted byte-identically, so a
v1.2 run can be compared against the v1.1 fingerprint instead of being compared
against itself.

Both source columns in the CSV mean different things and are mapped explicitly
rather than passed through: a KB incident keeps the source label ``coverage_matrix``
it has always had, because that string is a grouping key in the v1.1 results file.
"""
import csv
import json
from datetime import date
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
SOURCE = REPO / "data" / "coverage_matrix.csv"  # from s1.4, extended by s2.6
TARGET = REPO / "eval" / "evaluation_set.json"  # the output
VERSION = "2.0"   # 1.0 = coverage matrix only; 1.1 = + identifier probes; 1.2 = + manual stressors;
                  # 2.0 = graded against the manual-only KB (see retarget) + 6.1 "reported as" queries

# v1.1: identifier-only probes. A technician often pastes just the error code or
# hostname. Each token was extracted mechanically from the published article bodies
# in data/kb_dataset.json; ground truth = every article that contains it.
# Written BEFORE any ablation run; none removed afterwards.
IDENTIFIER_PROBES = [
    ("ERR_VPN_AUTH_004", ["KB0001"]),
    ("AADSTS50053", ["KB0005"]),
    ("APPSRV-OLD-04", ["KB0022"]),
    ("ERR_VPN_INSTALL_012", ["KB0026"]),
    ("MFA_TIMEOUT_003", ["KB0023"]),
    ("PWD_RESET_LOCKED", ["KB0018"]),
    ("SYNC_ERR_0091", ["KB0016"]),
    ("0x8004010F", ["KB0002", "KB0025"]),
    ("DISP_NOSIGNAL_04", ["KB0013", "KB0021"]),
    ("RFC_ERROR_COMMUNICATION", ["KB0008", "KB0022"]),
    # v2.0: identifiers the manual's KB articles cite (problem, known error, change and
    # related incident numbers), at least one per article, extracted mechanically from the
    # article text. The v1.1 tokens above are kept: none is in the manual, so they now test
    # that an unknown code is not answered. retarget() derives every probe's ground truth.
    ("PRB0040012", ["KB0001", "KB0005"]),
    ("INC0010024", ["KB0002"]),
    ("INC0010025", ["KB0003"]),
    ("INC0010026", ["KB0004"]),
    ("INC0010027", ["KB0005"]),
    ("RITM0010877", ["KB0006"]),
    ("INC0010029", ["KB0007"]),
    ("KE0000034", ["KB0008"]),
    ("PRB0040021", ["KB0009"]),
    ("PRB0040018", ["KB0010"]),
    ("MIR-2026-03", ["KB0010"]),
]

# v2.0: the manual's own article index (6.1) quotes how a requester reports each KB article.
# Copied from the manual, not written after looking at any ranking.
MANUAL_REPORTED_AS = [
    ("VPN says authentication failed since my password reset", ["KB0001"]),
    ("Outlook is Disconnected and no mail is arriving", ["KB0002"]),
    ("My mapped drive has disappeared since I logged in", ["KB0003"]),
    ("Jobs queue up and nothing comes out of the printer", ["KB0004"]),
    ("I am locked out and nothing lets me sign in", ["KB0005"]),
    ("I changed my phone and MFA no longer works", ["KB0006"]),
    ("My laptop has been slow since the update", ["KB0007"]),
    ("SAP times out with RFC_ERROR_COMMUNICATION", ["KB0008"]),
    ("Wi-Fi keeps dropping on the 5 GHz network", ["KB0009"]),
    ("Order service is returning 500s under load", ["KB0010"]),
]

KB_SOURCES = {"", "kb_incident"}


def manual_articles() -> dict[str, str]:
    """Article number -> text for every published manual section: what the KB holds since v2.0.
    Numbers are what publish_kb writes: the KB number, or the section label ("3.4")."""
    from src.retrieval.manual_parser import parse_manual
    return {s.kb_number or s.section_label: s.text for s in parse_manual() if s.workflow_state == "published"}


def retarget(items: list[dict], articles: dict[str, str]) -> list[dict]:
    """Grade against the articles that exist. The Sprint 1 corpus (KB0011+) was replaced by the
    manual: an incident only it answered is now unanswerable -- the agent must not invent a match --
    and a multi-doc incident keeps the articles that still exist. Identifier probes are re-derived
    the way they were made: ground truth = every article whose text contains the token."""
    for item in items:
        if item["source"] == "manual_stressor":
            continue
        if item["source"] == "identifier_probe":
            expected = sorted(n for n, text in articles.items() if item["query"] in text)
        else:
            expected = [a for a in item["expected_articles"] if a in articles]
        if expected != item["expected_articles"]:
            item["v1_expected_articles"] = item["expected_articles"]
        item["expected_articles"] = expected
        item["answerable"] = bool(expected)
        item["requires_multi_doc"] = len(expected) > 1
    return items


def _flag(row: dict, key: str) -> bool:
    return row[key].strip().lower() == "true"


def build_items(csv_path: Path) -> list[dict]:
    items: list[dict] = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["source"].strip() not in KB_SOURCES:
                items.append(_stressor_item(row))
                continue
            # "KB0013,KB0021" -> ["KB0013", "KB0021"];  "" -> []
            expected = [a.strip() for a in row["resolving_article_ids"].split(",") if a.strip()]
            items.append({
                "query_id": row["incident_id"],
                "query": row["incident_summary"],
                "expected_articles": expected,
                "answerable": _flag(row, "is_answerable"),
                "requires_multi_doc": _flag(row, "requires_multi_doc"),
                "source": "coverage_matrix",
            })
    for i, (token, expected) in enumerate(IDENTIFIER_PROBES, 1):
        items.append({
            "query_id": f"PROBE-{i:02d}",
            "query": token,
            "expected_articles": expected,
            "answerable": True,
            "requires_multi_doc": len(expected) > 1,
            "source": "identifier_probe",
        })
    for i, (query, expected) in enumerate(MANUAL_REPORTED_AS, 1):
        items.append({
            "query_id": f"REPORTED-{i:02d}",
            "query": query,
            "expected_articles": expected,
            "answerable": True,
            "requires_multi_doc": False,
            "source": "manual_reported_as",
        })
    return retarget(items, manual_articles())


def _stressor_item(row: dict) -> dict:
    """A manual stressor row.

    ``expected_articles`` is left empty on purpose.  The two axes are not
    comparable -- no KB number resolves to a manual section, and pretending
    otherwise would let a stressor row score as a hit if any chunk came back at
    all, which is the one thing it must not do.
    """
    section = row["expected_sections"].strip()
    return {
        "query_id": row["incident_id"],
        "query": row["incident_summary"],
        "expected_articles": [],
        "expected_sections": [section] if section else [],
        "capability_class": row["capability_class"].strip(),
        "expected_page": row["expected_page"].strip(),
        "requires_extractor": _flag(row, "requires_extractor"),
        "answerable": _flag(row, "is_answerable"),
        "requires_multi_doc": False,
        "source": "manual_stressor",
    }


def main():
    items = build_items(SOURCE)
    payload = {
        "version": VERSION,
        "source": str(SOURCE.relative_to(REPO)).replace("\\", "/"),
        "built": date.today().isoformat(),
        "counts": {
            "total": len(items),
            "answerable": sum(i["answerable"] for i in items),
            "unanswerable": sum(not i["answerable"] for i in items),
            "multi_doc": sum(i["requires_multi_doc"] for i in items),
            "by_source": {
                source: sum(i["source"] == source for i in items)
                for source in ("coverage_matrix", "identifier_probe", "manual_reported_as", "manual_stressor")
            },
            "stressor_answerable": sum(
                i["source"] == "manual_stressor" and i["answerable"] for i in items
            ),
        },
        "items": items,
    }
    TARGET.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)}  v{VERSION}  {payload['counts']}")


if __name__ == "__main__":
    main()
