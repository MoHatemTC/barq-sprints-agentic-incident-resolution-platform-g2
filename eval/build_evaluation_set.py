"""
Build the versioned evaluation set from S1.4's coverage matrix so data/coverage_matrix.csv  ->  eval/evaluation_set.json
The JSON is committed so the exam paper is frozen and versioned even if the CSV changes later.
to generate version Run:  python eval/build_evaluation_set.py
"""

import csv
import json
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "data" / "coverage_matrix.csv" # from s1.4
TARGET = REPO / "eval" / "evaluation_set.json" # the output 
VERSION = "1.1"   # 1.0 = coverage matrix only; 1.1 = + identifier probes

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
]


def build_items(csv_path: Path) -> list[dict]:
    items = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            # "KB0013,KB0021" -> ["KB0013", "KB0021"];  "" -> []
            expected = [a.strip() for a in row["resolving_article_ids"].split(",") if a.strip()]
            items.append({
                "query_id": row["incident_id"],
                "query": row["incident_summary"],
                "expected_articles": expected,
                "answerable": row["is_answerable"].strip().lower() == "true",
                "requires_multi_doc": row["requires_multi_doc"].strip().lower() == "true",
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
    return items


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
                "coverage_matrix": sum(i["source"] == "coverage_matrix" for i in items),
                "identifier_probe": sum(i["source"] == "identifier_probe" for i in items),
            },
        },
        "items": items,
    }
    TARGET.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)}  v{VERSION}  {payload['counts']}")


if __name__ == "__main__":
    main()
