"""
Single-command ablation harness (S2.4) to Runs every incident in eval/evaluation_set.json 
through the three retrieval modes and grades each against its ground-truth KB articles.
  python eval/ablation.py                 # k=5, one run , can custom it
it outputs  : eval/results/ablation_k<k>.md and .json
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import QDRANT, RETRIEVAL                     
from src.retrieval.hybrid_search import get_client, search   
from eval.planting import planted_numbers, planted_points    

MODES = ["dense", "hybrid", "hybrid_rerank"]
EVAL_SET = REPO / "eval" / "evaluation_set.json"
RESULTS = REPO / "eval" / "results"


#  grading func

def grade(got: list[str], expected: list[str]) -> dict:
    """got = KB numbers in rank order (chunks may repeat an article; we de-dupe)."""
    unique = list(dict.fromkeys(got))
    want = set(expected)
    hit = want & set(unique)
    first = next((i for i, n in enumerate(unique, 1) if n in want), None)
    return {
        "precision": len(hit) / len(unique) if unique else 0.0,
        "recall": len(hit) / len(want) if want else 0.0,
        "hit": bool(hit),
        "rr": 1.0 / first if first else 0.0,     # reciprocal rank
        "first_rank": first,
        "returned": unique,
    }


def run_mode(mode: str, items: list[dict], k: int, client) -> list[dict]:
    search(items[0]["query"], mode=mode, top_k=k, client=client)   # warm-up: model load is not timed
    rows = []
    for item in items:
        t0 = time.perf_counter()
        hits = search(item["query"], mode=mode, top_k=k, client=client)
        ms = (time.perf_counter() - t0) * 1000
        got = [h.number for h in hits]
        row = {
            "query_id": item["query_id"],
            "answerable": item["answerable"],
            "ms": round(ms, 1),
            "planted_hits": sorted(set(got) & planted_numbers()),
            "top_score": round(hits[0].score, 4) if hits else None,
            "returned": list(dict.fromkeys(got)),
        }
        if item["answerable"]:
            row.update(grade(got, item["expected_articles"]))
        rows.append(row)
    return rows


def summarise(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    lat = sorted(r["ms"] for r in rows)
    pct = statistics.quantiles(lat, n=100)          # pct[49] = p50, pct[94] = p95
    return {
        "precision": round(statistics.mean(r["precision"] for r in ans), 3),
        "recall": round(statistics.mean(r["recall"] for r in ans), 3),
        "hit_rate": round(statistics.mean(r["hit"] for r in ans), 3),
        "mrr": round(statistics.mean(r["rr"] for r in ans), 3),
        "planted_hits": sum(len(r["planted_hits"]) for r in rows),
        "p50_ms": round(pct[49], 1),
        "p95_ms": round(pct[94], 1),
    }


def sparse_wins(dense_rows: list[dict], hybrid_rows: list[dict], items: dict) -> list[dict]:
    """Answerable queries where hybrid found the article and dense missed it or ranked it worse."""
    wins = []
    for d, h in zip(dense_rows, hybrid_rows):
        if not d["answerable"]:
            continue
        if h["first_rank"] and (d["first_rank"] is None or h["first_rank"] < d["first_rank"]):
            item = items[d["query_id"]]
            wins.append({"query_id": d["query_id"], "query": item["query"],
                         "expected": item["expected_articles"],
                         "dense_rank": d["first_rank"], "hybrid_rank": h["first_rank"],
                         "dense_returned": d["returned"], "hybrid_returned": h["returned"]})
    return wins


def rankings(per_mode: dict) -> str:
    """Fingerprint of every ranking, to compare runs."""
    return json.dumps({m: [r["returned"] for r in rows] for m, rows in per_mode.items()})


# output report as MD 

def to_markdown(summary: dict, wins: list[dict], k: int, deterministic, budget: int, n_ans: int) -> str:
    base = summary["dense"]
    out = [f"## Ablation @k={k} — collection `{QDRANT.collection_name}`, {n_ans} answerable incidents", "",
           "| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |",
           "|---|---|---|---|---|---|---|---|"]
    for m in MODES:
        s = summary[m]
        out.append(f"| {m} | {s['precision']:.3f} | {s['recall']:.3f} | {s['hit_rate']:.3f} | {s['mrr']:.3f} "
                   f"| {s['planted_hits']} | {s['p50_ms']} | {s['p95_ms']} |")

    out += ["", "### Margin over dense baseline", "", "| mode | Δ precision | Δ recall | Δ MRR |", "|---|---|---|---|"]
    for m in MODES[1:]:
        s = summary[m]
        out.append(f"| {m} | {s['precision'] - base['precision']:+.3f} | {s['recall'] - base['recall']:+.3f} "
                   f"| {s['mrr'] - base['mrr']:+.3f} |")

    out += ["", f"### Latency headroom (budget {budget} ms)", "", "| mode | p95 ms | headroom ms | within budget |", "|---|---|---|---|"]
    for m in MODES:
        p95 = summary[m]["p95_ms"]
        out.append(f"| {m} | {p95} | {budget - p95:.1f} | {'yes' if p95 <= budget else 'NO'} |")

    out += ["", "### Sparse rescues dense (dense missed or ranked worse; hybrid found it)", ""]
    if wins:
        out += ["| incident | query | expected | dense rank | hybrid rank |", "|---|---|---|---|---|"]
        for w in wins:
            out.append(f"| {w['query_id']} | {w['query']} | {', '.join(w['expected'])} "
                       f"| {w['dense_rank'] or 'miss'} | {w['hybrid_rank']} |")
    else:
        out.append("_none at this k_")

    if deterministic is not None:
        out += ["", f"### Determinism: two full runs produced **{'IDENTICAL' if deterministic else 'DIFFERENT'}** rankings in every mode"]
    return "\n".join(out) + "\n"


# our main

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=RETRIEVAL.top_k)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--plant", action="store_true")
    ap.add_argument("--unplant", action="store_true")
    args = ap.parse_args()

    client = get_client()
    if args.unplant:
        client.delete(QDRANT.collection_name, points_selector=[p.id for p in planted_points()])
        print("decoys removed from", QDRANT.collection_name)
        return
    if args.plant:
        client.upsert(QDRANT.collection_name, planted_points())
        print("planted 3 decoys into", QDRANT.collection_name)

    data = json.load(open(EVAL_SET, encoding="utf-8"))
    items = data["items"]
    by_id = {i["query_id"]: i for i in items}
    print(f"evaluation set v{data['version']}: {len(items)} incidents, k={args.k}, runs={args.runs}\n")

    fingerprints, per_mode = [], {}
    for run in range(1, args.runs + 1):
        per_mode = {m: run_mode(m, items, args.k, client) for m in MODES}
        fingerprints.append(rankings(per_mode))
        for m in MODES:
            print(f"run {run}  {m:14s} {summarise(per_mode[m])}")
    deterministic = all(f == fingerprints[0] for f in fingerprints) if args.runs > 1 else None

    summary = {m: summarise(rows) for m, rows in per_mode.items()}
    wins = sparse_wins(per_mode["dense"], per_mode["hybrid"], by_id)
    n_ans = sum(i["answerable"] for i in items)
    md = to_markdown(summary, wins, args.k, deterministic, RETRIEVAL.latency_budget_ms, n_ans)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"ablation_k{args.k}.md").write_text(md, encoding="utf-8")
    (RESULTS / f"ablation_k{args.k}.json").write_text(json.dumps({
        "evaluation_set_version": data["version"], "k": args.k, "runs": args.runs,
        "deterministic": deterministic, "summary": summary, "sparse_wins": wins, "per_query": per_mode,
    }, indent=2), encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
