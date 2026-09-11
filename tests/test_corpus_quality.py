import csv
import json
from collections import defaultdict
from pathlib import Path

from src.retrieval.article_utils import dedupe_articles
from src.retrieval.sources.local_json_source import load_articles_from_json


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "data" / "kb_dataset.json"
COVERAGE_PATH = ROOT / "data" / "coverage_matrix.csv"


def _load_raw_articles():
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_corpus_has_required_article_volume_and_negative_records():
    articles = _load_raw_articles()
    states = {article["workflow_state"] for article in articles}

    assert len(articles) >= 25
    assert "draft" in states
    assert "retired" in states


def test_corpus_has_at_least_two_retired_to_published_version_pairs():
    versions_by_number = defaultdict(list)
    for article in _load_raw_articles():
        versions_by_number[article["article_number"]].append(article)

    version_pairs = []
    for number, articles in versions_by_number.items():
        versions = {article["version"] for article in articles}
        states = {article["workflow_state"] for article in articles}
        if len(versions) >= 2 and {"retired", "published"}.issubset(states):
            version_pairs.append(number)

    assert set(version_pairs) >= {"KB0010", "KB0022"}
    assert len(version_pairs) >= 2


def test_dedupe_keeps_highest_non_retired_version_for_version_pairs():
    selected = {
        article["article_number"]: article
        for article in dedupe_articles(_load_raw_articles())
    }

    assert selected["KB0010"]["version"] == 2
    assert selected["KB0010"]["workflow_state"] == "published"
    assert selected["KB0022"]["version"] == 2
    assert selected["KB0022"]["workflow_state"] == "published"


def test_local_loader_uses_deduped_current_articles():
    articles = load_articles_from_json(str(CORPUS_PATH))
    selected = {article.number: article for article in articles}

    assert selected["KB0010"].version == 2
    assert selected["KB0022"].version == 2
    assert len([article for article in articles if article.number == "KB0022"]) == 1


def test_coverage_matrix_represents_multi_doc_and_unanswerable_incidents():
    rows = list(csv.DictReader(COVERAGE_PATH.open(encoding="utf-8")))
    multi_doc = [row for row in rows if row["requires_multi_doc"] == "true"]
    unanswerable = [row for row in rows if row["is_answerable"] == "false"]

    assert len(multi_doc) >= 5
    assert len(unanswerable) >= 5
    assert any(row["incident_id"] == "INC1028" and row["resolving_article_ids"] == "KB0022" for row in rows)
