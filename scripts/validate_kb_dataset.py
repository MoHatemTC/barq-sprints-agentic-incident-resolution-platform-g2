"""
Validates a supplied KB articles JSON dataset against the required schema
before it's normalized into the ingestion pipeline (Path B, S1.4).

Usage: python scripts/validate_kb_dataset.py path/to/dataset.json
"""

import json
import sys
from collections import Counter

REQUIRED_FIELDS = ["article_number", "title", "category", "service",
                    "workflow_state", "version", "security_level", "body"]


def validate(dataset_path: str):
    with open(dataset_path, "r", encoding="utf-8") as f:
        articles = json.load(f)

    print(f"Loaded {len(articles)} articles from {dataset_path}\n")

    issues = {
        "missing_fields": [],
        "empty_fields": [],
        "duplicate_article_numbers": [],
        "category_values": Counter(),
        "service_values": Counter(),
        "missing_security_level": [],
        "html_present": [],
    }

    seen_numbers = set()

    for i, article in enumerate(articles):
        num = article.get("article_number", f"<no article_number, index {i}>")

        missing = [f for f in REQUIRED_FIELDS if f not in article]
        if missing:
            issues["missing_fields"].append((num, missing))

        empty = [f for f in REQUIRED_FIELDS if f in article and not str(article[f]).strip()]
        if empty:
            issues["empty_fields"].append((num, empty))

        if num in seen_numbers:
            issues["duplicate_article_numbers"].append(num)
        seen_numbers.add(num)

        if "category" in article:
            issues["category_values"][article["category"]] += 1
        if "service" in article:
            issues["service_values"][article["service"]] += 1

        if not article.get("security_level"):
            issues["missing_security_level"].append(num)

        body = article.get("body", "")
        if "<" in body and ">" in body:
            issues["html_present"].append(num)

    _print_report(issues)
    return issues


def _print_report(issues: dict):
    print("=== VALIDATION REPORT ===\n")

    print(f"Articles missing required fields: {len(issues['missing_fields'])}")
    for num, fields in issues["missing_fields"]:
        print(f"  - {num}: missing {fields}")

    print(f"\nArticles with empty (but present) fields: {len(issues['empty_fields'])}")
    for num, fields in issues["empty_fields"]:
        print(f"  - {num}: empty {fields}")

    print(f"\nDuplicate article_numbers: {len(issues['duplicate_article_numbers'])}")
    for num in issues["duplicate_article_numbers"]:
        print(f"  - {num}")

    print(f"\nArticles missing security_level specifically: {len(issues['missing_security_level'])}")
    for num in issues["missing_security_level"]:
        print(f"  - {num}")

    print(f"\nDistinct 'category' values seen (check for inconsistent naming):")
    for val, count in issues["category_values"].most_common():
        print(f"  - '{val}': {count}")

    print(f"\nDistinct 'service' values seen (check for inconsistent naming):")
    for val, count in issues["service_values"].most_common():
        print(f"  - '{val}': {count}")

    print(f"\nArticles containing raw HTML in body: {len(issues['html_present'])}")
    for num in issues["html_present"]:
        print(f"  - {num}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_kb_dataset.py path/to/dataset.json")
        sys.exit(1)
    validate(sys.argv[1])