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
VERSION = "1.0"


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
        },
        "items": items,
    }
    TARGET.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)}  v{VERSION}  {payload['counts']}")


if __name__ == "__main__":
    main()
