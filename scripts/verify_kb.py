"""Read-only check: is the BARQ manual correctly published to ServiceNow?
    python -m scripts.verify_kb
"""
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")
from src.config import PATHS, SERVICENOW
from src.retrieval.manual_parser import parse_manual
from src.retrieval.publish_kb import _build_payload, _mismatched_fields, section_to_article
from src.retrieval.servicenow_auth import ServiceNowOAuthClient

sections = parse_manual()
articles = [section_to_article(s) for s in sections]
expected = [a for a in articles if a.workflow_state == "published"]
skipped = [a for a in articles if a.workflow_state != "published"]
pdf_chars = sum(len(s.text) for s in sections)

headers = ServiceNowOAuthClient().auth_headers()
fields = sorted(set(_build_payload(expected[0])) | {"number", "sys_id"})
records, offset = [], 0
with httpx.Client(timeout=60) as c:
    while True:
        r = c.get(f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}", headers=headers, params={
            "sysparm_query": f"kb_knowledge_base={SERVICENOW.kb_sys_id}^ORDERBYnumber",
            "sysparm_fields": ",".join(fields), "sysparm_exclude_reference_link": "true",
            "sysparm_limit": 100, "sysparm_offset": offset})
        r.raise_for_status()
        batch = r.json()["result"]
        records += batch
        if len(batch) < 100:
            break
        offset += 100

by_title = defaultdict(list)
for rec in records:
    by_title[rec["short_description"]].append(rec)

missing, dupes, not_published, content_bad = [], [], [], []
ok = 0
for a in expected:
    recs = by_title.get(a.title, [])
    if not recs:
        missing.append(a.article_id); continue
    if len(recs) > 1:
        dupes.append((a.article_id, [x["number"] for x in recs]))
    rec = recs[0]
    if rec["workflow_state"] != "published":
        not_published.append((a.article_id, rec["number"], rec["workflow_state"]))
    bad = _mismatched_fields(_build_payload(a), rec)
    if bad:
        content_bad.append((a.article_id, rec["number"], bad))
    elif rec["workflow_state"] == "published" and len(recs) == 1:
        ok += 1

expected_titles = {a.title for a in expected}
extra = [(r["number"], r["short_description"][:60]) for r in records if r["short_description"] not in expected_titles]
sn_chars = sum(len(html.unescape(r.get("text") or "")) for r in records if r["short_description"] in expected_titles)

mapping_path = Path(PATHS.servicenow_kb_mapping)
mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}
sys_ids = {r["sys_id"] for r in records}
mapping_ok = sum(1 for a in expected if mapping.get(a.article_id) in sys_ids)

print(f"PDF: {len(sections)} sections, {pdf_chars} chars -> {len(expected)} to publish, "
      f"{len(skipped)} skipped on purpose: {[a.article_id for a in skipped]}")
print(f"ServiceNow KB: {len(records)} articles")
print(f"  fully OK (once, published, text+metadata identical to PDF): {ok}/{len(expected)}")
print(f"  missing: {len(missing)} {missing}")
print(f"  duplicated: {len(dupes)} {dupes}")
print(f"  not published: {len(not_published)} {not_published}")
print(f"  content differs from PDF: {len(content_bad)} {content_bad}")
print(f"  extra (not from the manual): {len(extra)} {extra}")
print(f"  text chars in ServiceNow vs PDF (published sections): {sn_chars} vs "
      f"{sum(len(a.body) for a in expected)}")
all_good = ok == len(expected) and not extra
print(f"mapping file: {len(mapping)} entries, {mapping_ok}/{len(expected)} point at a live record")
print("\nRESULT:", "ALL GOOD" if all_good else "PROBLEMS FOUND (see above)")
sys.exit(0 if all_good else 1)
