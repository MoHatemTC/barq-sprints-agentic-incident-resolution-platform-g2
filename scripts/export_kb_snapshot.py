"""Read-only: freeze the published ServiceNow KB into data/kb_dataset.json.

CI never talks to ServiceNow. The eval gate ingests this committed snapshot
(`python -m src.retrieval.ingest local`), so its scores are reproducible.
Re-run and commit when the KB changes, then refresh eval/thresholds.json.

    python -m scripts.export_kb_snapshot
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
from src.config import PATHS
from src.retrieval.ingest import save_articles_to_json
from src.retrieval.sources.servicenow_source import load_articles_from_servicenow

articles = load_articles_from_servicenow()
if not articles:
    sys.exit("ServiceNow returned 0 published articles; snapshot not written.")
save_articles_to_json(articles, PATHS.corpus_json)
print(f"wrote {len(articles)} articles to {PATHS.corpus_json}")
