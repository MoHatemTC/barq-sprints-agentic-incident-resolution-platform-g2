"""
Regenerate data/coverage_matrix.csv from the S1.4 baseline plus the S2.6 stressor rows.

The baseline 26 incident rows are copied through from the restored file so they
stay byte-identical: an evaluation set that drifts from the coverage matrix it
claims to be built from is not a baseline, it is a second experiment wearing the
first one's name.  The stressor rows are held here as data rather than typed
into the CSV, because a hand-edited CSV with 47 rows and 11 columns is one typo
away from a silently mis-parsed file -- which is exactly what happened once.

Run: python tools/build_coverage_matrix.py
"""
from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "data" / "coverage_matrix_s14_baseline.csv"
TARGET = REPO / "data" / "coverage_matrix.csv"

FIELDS = [
    "incident_id", "incident_summary", "resolving_article_ids", "is_answerable",
    "requires_multi_doc", "resolution_status", "source", "capability_class",
    "expected_sections", "expected_page", "requires_extractor",
]

# (id, query, answerable, resolution_status, capability_class, section, page)
# Every answerable row's section was read out of the extracted table or form
# before the query was written, so the ground truth is the extractor's output
# and not an assumption about what the page probably says.
STRESSORS: list[tuple[str, str, bool, str, str, str, str]] = [
    ("STR-01",
     "how many analysts cover Dubai out of hours and what are the hours", True,
     "Answerable only by reading the merged header of the coverage table on p8 as one "
     "header over four columns",
     "merged_header", "2.1", "8"),
    ("STR-02",
     "what time does the Cairo desk start on a Saturday and how many analysts are on", True,
     "Saturday row under the spanned CAIRO header of the p8 coverage table",
     "merged_header", "2.1", "8"),
    ("STR-03",
     "which priority states pause the SLA clock and who may set them", True,
     "On Hold and Pending Caller pause the clock; the p10 state table has a fifth "
     "WHO MAY SET IT column that a line-by-line reading loses",
     "table", "3.3", "10"),
    ("STR-04",
     "who owns the sap-erp service and in which window is it maintained", True,
     "The ownership cell of the p15 catalogue row carries a nested Owner/Group/Window table",
     "nested_table", "5.2", "15"),
    ("STR-05",
     "what is the maintenance window for the identity service and who is its owner", True,
     "Only the nested FIELD/VALUE tables inside the p15 catalogue rows hold the window",
     "nested_table", "5.2", "15"),
    ("STR-06",
     "what was the last entry in the INC0010023 journal and what time was it", True,
     "The journal runs off the foot of p25 and finishes on p26; the last entry is only "
     "on the continuation page",
     "page_crossing", "7.1", "26"),
    ("STR-07",
     "which work note closed the VPN incident and how long did the whole thing take", True,
     "Needs the p25 journal and the p26 continuation stitched into one table to see the close",
     "page_crossing", "7.1", "26"),
    ("STR-08",
     "what assignment group and assignee are recorded on incident INC0010023", True,
     "The p25 incident form is label-above-value pairs; read as lines the labels detach "
     "from their values",
     "form_parsing", "7.1", "25"),
    ("STR-09",
     "what impact and urgency did the caller record on INC0010023", True,
     "The form's combined Impact / Urgency field",
     "key_value", "7.1", "25"),
    ("STR-10",
     "was there an SLA breach on INC0010023 and against what target", True,
     "The form's Breach field pairs a 48 minute closure with a 3-day target",
     "key_value", "7.1", "25"),
    ("STR-11",
     "what is the difference between a problem and a known error in this manual", True,
     "p29 states the distinction in a two-column comparison table with a callout box under it",
     "table", "8.3", "29"),
    ("STR-12",
     "which known error covers the SAP GUI RFC timeout and what is its permanent fix", True,
     "This is the known-error register on p30 and not the problem register on p29; "
     "answering from p29 is the failure this row measures",
     "table", "8.3", "30"),
    ("STR-13",
     "what approval record covers CHG0030455 against the incident", True,
     "10.4 is a raster screenshot; the section is findable and the payload must say the "
     "pixels were not read",
     "ocr", "10.4", "35"),
    ("STR-14",
     "which agent stages screen the incident text for embedded instructions", True,
     "p41 is two columns read left then right; the enforcement stage list is in the "
     "right column",
     "layout", "11.7", "41"),
    ("STR-15",
     "what is the contractual SLA attainment measure and what is it used for", True,
     "The p44 measure table has a DEFINITION column and a WHAT IT IS USED FOR column",
     "table", "12.2", "44"),
    ("STR-16",
     "what counts as impact 2 and what counts as urgency 2", True,
     "Appendix C's two-column scale",
     "merged_header", "Appendix C", "49"),
    ("STR-17",
     "should impact be counted by how many people are blocked or by how many are "
     "inconvenienced", True,
     "The prose line under the Appendix C scale reverses the usual reading",
     "layout", "Appendix C", "49"),
    ("STR-18",
     "how do I read the run log for the integration failure in the appendix", True,
     "11.8's run log is a dark-theme screenshot; findable as a section, not readable "
     "as text",
     "ocr", "11.8", "42"),
    ("STR-19",
     "is there a documented resolution target for the temperature sensor fleet", False,
     "Intentionally unanswerable: no section of the manual covers a sensor fleet",
     "unanswerable", "", ""),
    ("STR-20",
     "what is the retention period for closed incident records after closure", False,
     "Intentionally unanswerable: the manual sets no retention period",
     "unanswerable", "", ""),
]


def build() -> list[dict[str, str]]:
    with open(BASELINE, encoding="utf-8", newline="") as f:
        baseline = list(csv.DictReader(f))

    rows: list[dict[str, str]] = []
    for row in baseline:
        kept = {k: row[k] for k in row}
        kept.update({"source": "kb_incident", "capability_class": "",
                     "expected_sections": "", "expected_page": "", "requires_extractor": ""})
        rows.append(kept)

    for iid, query, answerable, status, klass, section, page in STRESSORS:
        rows.append({
            "incident_id": iid, "incident_summary": query, "resolving_article_ids": "",
            "is_answerable": str(answerable).lower(), "requires_multi_doc": "false",
            "resolution_status": status, "source": "manual_stressor",
            "capability_class": klass, "expected_sections": section,
            "expected_page": page, "requires_extractor": "true" if answerable else "",
        })
    return rows


def main() -> None:
    ids = [iid for iid, *_ in STRESSORS]
    assert len(ids) == len(set(ids)), f"duplicate stressor ids: {ids}"
    rows = build()
    with open(TARGET, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    written = list(csv.DictReader(open(TARGET, encoding="utf-8", newline="")))
    assert len(written) == len(rows), f"round-trip lost rows: {len(written)} != {len(rows)}"
    assert all(r["source"] == "kb_incident" for r in written[:26])
    print(f"wrote {len(rows)} rows "
          f"({sum(r['source'] == 'kb_incident' for r in rows)} kb + "
          f"{sum(r['source'] == 'manual_stressor' for r in rows)} stressor)")


if __name__ == "__main__":
    main()
