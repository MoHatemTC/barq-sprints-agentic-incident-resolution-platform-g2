"""CI evaluation gate (FR-20): fail the build when a score drops below its floor.

Reads the results eval/ablation.py just wrote and compares every metric named
in eval/thresholds.json against its floor. Exit 1 on any regression, 0 otherwise.

  python eval/gate.py                       # uses eval/thresholds.json
  python eval/gate.py --thresholds other.json

Adding a metric (e.g. LLM-judge faithfulness) needs no code change here:
write it into the results JSON under summary.<mode>.<metric> and give it a
floor in thresholds.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "eval" / "results"


def check(results: dict, thresholds: dict) -> tuple[list[dict], list[str]]:
    """Return (rows, problems). A row per mode/metric; problems are failure reasons."""
    problems = []
    if str(results.get("evaluation_set_version")) != str(thresholds["evaluation_set_version"]):
        problems.append(
            f"evaluation set is v{results.get('evaluation_set_version')} but thresholds are for "
            f"v{thresholds['evaluation_set_version']}; re-baseline eval/thresholds.json"
        )

    tolerance = thresholds.get("tolerance", 0.0)
    rows = []
    for mode, floors in thresholds["floors"].items():
        scores = results["summary"].get(mode)
        if scores is None:
            problems.append(f"mode '{mode}' missing from results")
            continue
        for metric, floor in floors.items():
            value = scores.get(metric)
            ok = value is not None and value >= floor - tolerance
            rows.append({"mode": mode, "metric": metric, "value": value, "floor": floor, "ok": ok})
            if not ok:
                problems.append(f"{mode}.{metric} = {value} is below floor {floor} (tolerance {tolerance})")
    return rows, problems


def to_markdown(rows: list[dict], problems: list[str], k: int) -> str:
    lines = [f"### Evaluation gate (k={k}): {'FAILED' if problems else 'passed'}", "",
             "| mode | metric | score | floor | |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| `{r['mode']}` | {r['metric']} | {r['value']} | {r['floor']} | {'✅' if r['ok'] else '❌'} |")
    if problems:
        lines += ["", *[f"- {p}" for p in problems]]
    return "\n".join(lines) + "\n"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", default=str(REPO / "eval" / "thresholds.json"))
    args = ap.parse_args()

    thresholds = json.loads(Path(args.thresholds).read_text(encoding="utf-8"))
    k = thresholds["k"]
    results = json.loads((RESULTS / f"ablation_k{k}.json").read_text(encoding="utf-8"))

    rows, problems = check(results, thresholds)
    md = to_markdown(rows, problems, k)
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
