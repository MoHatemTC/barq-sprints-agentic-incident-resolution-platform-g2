import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import QDRANT, RETRIEVAL
from src.retrieval.hybrid_search import get_client, search
from src.retrieval.manual_parser import ids_for
from eval.adapters import score_retrieval, slice_report, turns
from eval.ablation_manual import MODES, sparse_wins, summarise, to_markdown

RESULTS = REPO / "eval" / "results"
KB_TITLE = re.compile(r"^(6\.\d+) (KB\d{4}) v(\d+)")   # "6.13 KB0010 v2 KB0010 – Order service ..."


def kb_ids(payload: dict) -> set[str]:
    """Dataset ids one KB chunk satisfies: "KB0010" v2 -> {"6.13", "6.13 KB0010 v2"}; "Appendix B.1" -> also "Appendix B"."""
    m = KB_TITLE.match(payload.get("title", ""))
    if m:
        section, number, version = m.groups()
        return {section, f"{section} {number} v{version}"}
    return ids_for(payload["number"], payload["number"])


def label(payload: dict) -> str:
    m = KB_TITLE.match(payload.get("title", ""))
    return f"{payload['number']} ({m.group(1)})" if m else payload["number"]


def run_mode(mode: str, all_turns: list[dict], k: int, client) -> list[dict]:
    search(all_turns[0]["standalone_input"], mode=mode, top_k=k, client=client)   # warm-up, not timed
    rows = []
    for t in all_turns:
        t0 = time.perf_counter()
        hits = search(t["standalone_input"], mode=mode, top_k=k, client=client)
        ms = (time.perf_counter() - t0) * 1000
        got = set().union(*(kb_ids(h.payload) for h in hits)) if hits else set()
        row = score_retrieval(got, t)
        row.update({"ms": round(ms, 1), "behaviour": t["expected_behaviour"], "requires": t["requires"],
                    "returned": [label(h.payload) for h in hits],
                    "passed": bool(row["top_k_contains_all"]) and row["clean"]})
        rows.append(row)
    return rows


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=RETRIEVAL.top_k)
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()

    client = get_client()
    all_turns = turns()
    by_id = {t["turn_id"]: t for t in all_turns}
    print(f"{len(all_turns)} turns, k={args.k}, runs={args.runs}, collection={QDRANT.collection_name}\n")

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
    md = md.replace(f"## Manual ablation @k={args.k} — collection `{RETRIEVAL.manual_collection_name}`",
                    f"## KB ablation @k={args.k} — collection `{QDRANT.collection_name}`", 1)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"kb_ablation_k{args.k}.md").write_text(md, encoding="utf-8")
    (RESULTS / f"kb_ablation_k{args.k}.json").write_text(json.dumps({
        "collection": QDRANT.collection_name, "k": args.k, "runs": args.runs, "deterministic": deterministic,
        "summary": summary, "by_capability": slices, "sparse_wins": wins, "per_turn": per_mode},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
