"""
S2.4 evalution matrix ablation: the mentor's 100-turn dataset against the manual collection
to run : python eval/ablation_manual.py , that output eval/results/manual_ablation_k<k>.md and .json
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import RETRIEVAL                              
from src.retrieval.hybrid_search import get_client, search    
from src.retrieval.manual_parser import ids_for               
from eval.adapters import score_retrieval, slice_report, turns

MODES = ["dense", "hybrid", "hybrid_rerank"]
RESULTS = REPO / "eval" / "results"


def retrieved_ids(hits) -> set[str]:
    """Every section id the returned chunks satisfy (own id, label, parent appendix)."""
    return set().union(*(ids_for(h.payload["section_id"], h.payload["section_label"]) for h in hits)) if hits else set()


def run_mode(mode: str, all_turns: list[dict], k: int, client) -> list[dict]:
    search(all_turns[0]["standalone_input"], mode=mode, top_k=k, client=client,
           collection=RETRIEVAL.manual_collection_name)                      # warm-up, not timed
    rows = []
    for t in all_turns:
        t0 = time.perf_counter()
        hits = search(t["standalone_input"], mode=mode, top_k=k, client=client,
                      collection=RETRIEVAL.manual_collection_name)
        ms = (time.perf_counter() - t0) * 1000
        row = score_retrieval(retrieved_ids(hits), t)
        row.update({"ms": round(ms, 1), "behaviour": t["expected_behaviour"], "requires": t["requires"],
                    "returned": [h.payload["section_label"] for h in hits],
                    "passed": bool(row["top_k_contains_all"]) and row["clean"]})
        rows.append(row)
    return rows


def summarise(rows: list[dict]) -> dict:
    """Averages over turns that expect an answer; clean rate over all turns."""
    ans = [r for r in rows if r["recall"] is not None]
    lat = sorted(r["ms"] for r in rows)
    pct = statistics.quantiles(lat, n=100) if len(lat) > 1 else lat * 100
    return {
        "n_answerable": len(ans),
        "precision": round(statistics.mean(r["precision"] for r in ans), 3),
        "recall": round(statistics.mean(r["recall"] for r in ans), 3),
        "all_expected_found": round(statistics.mean(r["top_k_contains_all"] for r in ans), 3),
        "clean_rate": round(statistics.mean(r["clean"] for r in rows), 3),
        "forbidden_hits": sum(len(r["forbidden_retrieved"]) for r in rows),
        "p50_ms": round(pct[49], 1), "p95_ms": round(pct[94], 1),
    }


def sparse_wins(dense_rows, hybrid_rows, by_id) -> list[dict]:
    """Turns where hybrid retrieved an expected section that dense's top-k lacked."""
    wins = []
    for d, h in zip(dense_rows, hybrid_rows):
        if d["recall"] is None or h["recall"] <= d["recall"]:
            continue
        t = by_id[d["turn_id"]]
        wins.append({"turn_id": d["turn_id"], "query": t["standalone_input"], "expected": t["expected_sections"],
                     "requires": t["requires"], "dense_recall": d["recall"], "hybrid_recall": h["recall"],
                     "dense_returned": d["returned"], "hybrid_returned": h["returned"]})
    return wins


def to_markdown(summary, by_mode_slices, wins, k, deterministic, budget) -> str:
    out = [f"## Manual ablation @k={k} — collection `{RETRIEVAL.manual_collection_name}`, 100 turns "
           f"({summary['dense']['n_answerable']} expect an answer)", "",
           "| mode | precision@k | recall@k | all expected found | clean rate | forbidden hits | p50 ms | p95 ms |",
           "|---|---|---|---|---|---|---|---|"]
    for m in MODES:
        s = summary[m]
        out.append(f"| {m} | {s['precision']:.3f} | {s['recall']:.3f} | {s['all_expected_found']:.3f} | {s['clean_rate']:.3f} "
                   f"| {s['forbidden_hits']} | {s['p50_ms']} | {s['p95_ms']} |")
    base = summary["dense"]
    out += ["", "### Margin over dense baseline", "", "| mode | Δ precision | Δ recall | Δ all-found |", "|---|---|---|---|"]
    for m in MODES[1:]:
        s = summary[m]
        out.append(f"| {m} | {s['precision'] - base['precision']:+.3f} | {s['recall'] - base['recall']:+.3f} "
                   f"| {s['all_expected_found'] - base['all_expected_found']:+.3f} |")
    out += ["", f"### Latency headroom (budget {budget} ms)", "", "| mode | p95 ms | headroom | within budget |", "|---|---|---|---|"]
    for m in MODES:
        p95 = summary[m]["p95_ms"]
        out.append(f"| {m} | {p95} | {budget - p95:.1f} | {'yes' if p95 <= budget else 'NO'} |")

    out += ["", "### Pass rate by capability (turn passes = all expected sections found and nothing forbidden)", "",
            "| capability | n | " + " | ".join(MODES) + " |", "|---|---|" + "---|" * len(MODES)]
    caps = sorted(by_mode_slices["dense"], key=lambda c: -by_mode_slices["dense"][c]["total"])
    for c in caps:
        n = by_mode_slices["dense"][c]["total"]
        out.append(f"| {c} | {n} | " + " | ".join(f"{by_mode_slices[m][c]['rate']:.2f}" for m in MODES) + " |")

    out += ["", "### Sparse rescues dense (hybrid recall > dense recall on the same turn)", ""]
    if wins:
        out += ["| turn | query | expected | requires | dense recall | hybrid recall |", "|---|---|---|---|---|---|"]
        for w in wins:
            out.append(f"| {w['turn_id']} | {w['query']} | {', '.join(w['expected'])} | {', '.join(w['requires'])} "
                       f"| {w['dense_recall']:.2f} | {w['hybrid_recall']:.2f} |")
    else:
        out.append("_none at this k_")
    if deterministic is not None:
        out += ["", f"### Determinism: two full runs produced **{'IDENTICAL' if deterministic else 'DIFFERENT'}** rankings in every mode"]
    return "\n".join(out) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=RETRIEVAL.top_k)
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()

    client = get_client()
    all_turns = turns()
    by_id = {t["turn_id"]: t for t in all_turns}
    print(f"{len(all_turns)} turns, k={args.k}, runs={args.runs}, collection={RETRIEVAL.manual_collection_name}\n")

    fingerprints, per_mode = [], {}
    for run in range(1, args.runs + 1):
        per_mode = {m: run_mode(m, all_turns, args.k, client) for m in MODES}
        fingerprints.append(json.dumps({m: [r["returned"] for r in rows] for m, rows in per_mode.items()}))
        for m in MODES:
            print(f"run {run}  {m:14s} {summarise(per_mode[m])}")
    deterministic = all(f == fingerprints[0] for f in fingerprints) if args.runs > 1 else None

    summary = {m: summarise(rows) for m, rows in per_mode.items()}
    slices = {m: slice_report(rows) for m, rows in per_mode.items()}
    wins = sparse_wins(per_mode["dense"], per_mode["hybrid"], by_id)
    md = to_markdown(summary, slices, wins, args.k, deterministic, RETRIEVAL.latency_budget_ms)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"manual_ablation_k{args.k}.md").write_text(md, encoding="utf-8")
    (RESULTS / f"manual_ablation_k{args.k}.json").write_text(json.dumps({
        "k": args.k, "runs": args.runs, "deterministic": deterministic, "summary": summary,
        "by_capability": slices, "sparse_wins": wins, "per_turn": per_mode}, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
