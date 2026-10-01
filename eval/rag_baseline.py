from __future__ import annotations

import json
from pathlib import Path

from eval.adapters import turns, score_retrieval
from src.retrieval.hybrid_search import search


def run_rag():
    results = []

    for i, t in enumerate(turns(), 1):
        query = t["standalone_input"]

        try:
            chunks = search(
                query=query,
                collection="barq_manual",
            )

            retrieved_ids = [
                str(
                    c.payload.get("section_label")
                    or c.number
                    or c.point_id
                )
                for c in chunks
            ]

            score = score_retrieval(retrieved_ids, t)

            results.append({
                "turn_id": t["turn_id"],
                "expected_sections": t["expected_sections"],
                "retrieved_sections": retrieved_ids,
                **score,
            })

            print(
                f"[{i}/{len(turns())}] "
                f"{t['turn_id']} "
                f"recall={score['recall']} "
                f"precision={score['precision']}"
            )

        except Exception as e:
            print(f"[ERROR] {t['turn_id']}: {e}")

            results.append({
                "turn_id": t["turn_id"],
                "recall": 0.0,
                "precision": 0.0,
                "top_k_contains_all": False,
                "forbidden_retrieved": [],
                "clean": False,
                "error": str(e),
            })

    valid = [r for r in results if "error" not in r]

    mean_recall = (
        sum(r["recall"] or 0 for r in valid) / len(valid)
        if valid else 0
    )

    mean_precision = (
        sum(r["precision"] for r in valid) / len(valid)
        if valid else 0
    )

    hit_rate = (
        sum(bool(r["top_k_contains_all"]) for r in valid) / len(valid)
        if valid else 0
    )

    clean_rate = (
        sum(bool(r["clean"]) for r in valid) / len(valid)
        if valid else 0
    )

    report = {
        "total_turns": len(results),
        "successful_turns": len(valid),
        "failed_turns": len(results) - len(valid),
        "mean_recall": round(mean_recall, 4),
        "mean_precision": round(mean_precision, 4),
        "hit_rate": round(hit_rate, 4),
        "clean_rate": round(clean_rate, 4),
        "results": results,
    }

    output = Path("eval/results/rag_baseline.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n================ RAG BASELINE ================")
    print(f"Turns:           {report['total_turns']}")
    print(f"Successful:      {report['successful_turns']}")
    print(f"Failed:          {report['failed_turns']}")
    print(f"Mean Recall:     {report['mean_recall']}")
    print(f"Mean Precision:  {report['mean_precision']}")
    print(f"Hit Rate:        {report['hit_rate']}")
    print(f"Clean Rate:      {report['clean_rate']}")
    print(f"\nSaved to: {output}")


if __name__ == "__main__":
    run_rag()