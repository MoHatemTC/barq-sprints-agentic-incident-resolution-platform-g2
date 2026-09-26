"""
Single-command ablation harness (S2.4) to Runs every incident in eval/evaluation_set.json 
through the three retrieval modes and grades each against its ground-truth KB articles.
  python eval/ablation.py                 # k=5, one run , can custom it
it outputs  : eval/results/ablation_k<k>.md and .json

S2.6 adds a second question, asked of the manual rather than the KB: do the
extractors earn their place?  The manual is read twice -- once by
``ingest_manual``, which reads a page as a column of lines, and once by
``ingest_stressors``, which reads it as tables, forms and reading order -- and
the stressor rows are run against both.

  python eval/ablation.py --stressors

Both readings are indexed into QDRANT.collection_name, alongside the KB
articles, and an arm is a filter over that one collection:

  baseline   is_stressor absent      the KB articles, which is what was there
                                     before this sprint
  stressor   is_stressor = true      the manual's extracted pages
  combined   no filter               everything, which is what a live query gets

The three arms have to share a search space.  With the manual's points in a
collection of their own, a KB query could not see them, "no regression" and "the
extractors help" both came out automatic, and neither was ever measured.
``--regression`` is the other half of this: the 37 baseline rows scored with and
without the manual's pages eligible, which is where adding them does cost
something.

``grade`` and ``sparse_wins`` are unchanged: the KB half of the set is the
baseline the earlier sprints measured, and its fingerprint is the no-regression
check for the KB path.
"""

import argparse
import collections
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import QDRANT, RETRIEVAL                     
from src.retrieval.filters import RetrievalFilters
from src.retrieval.hybrid_search import get_client, search
from eval.planting import planted_numbers, planted_points    

MODES = ["dense", "hybrid", "hybrid_rerank"]
EVAL_SET = REPO / "eval" / "evaluation_set.json"
RESULTS = REPO / "eval" / "results"

# S2.6: the three arms. The manual is indexed twice -- ingest_manual reads a page
# as lines, ingest_stressors reads it through the table/form/layout extractors --
# and both readings plus the KB articles live in QDRANT.collection_name. So an arm
# is a filter over one collection, not a different collection.
#
# This is what makes the comparison mean anything. With a separate collection per
# arm, a baseline query could not see a stressor point, so "no regression" and
# "the extractors are good" were both automatic and neither was ever tested.
# Here the stressor points take up slots in the same top-k, which is the point.
MANUAL_ARMS = {
    "baseline": RetrievalFilters(is_stressor=False),   # KB articles only
    "stressor": RetrievalFilters(is_stressor=True),    # the manual's extracted pages
    "combined": RetrievalFilters(),                   # everything, as a live query sees it
}

# Spelled out in the report so a reader can tell an arm that found nothing because
# nothing was eligible from an arm that found nothing because the extractor failed.
ARM_ELIGIBILITY = {
    "baseline": "KB articles only (`is_stressor` absent)",
    "stressor": "extracted pages only (`is_stressor` = true)",
    "combined": "KB articles + extracted pages",
}


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


def run_mode(mode: str, items: list[dict], k: int, client, filters=None) -> list[dict]:
    # filters: None searches the whole collection, which is what a live query gets.
    # RetrievalFilters(is_stressor=False) restricts to the KB articles, and is how
    # the S2.6 regression check scores the same 37 queries with and without the
    # manual's extracted pages taking up slots in the same top-k.
    search(items[0]["query"], mode=mode, top_k=k, client=client, filters=filters)  # warm-up: not timed
    rows = []
    for item in items:
        t0 = time.perf_counter()
        hits = search(item["query"], mode=mode, top_k=k, client=client, filters=filters)
        ms = (time.perf_counter() - t0) * 1000
        got = [h.number for h in hits]
        row = {
            "query_id": item["query_id"],
            "source": item.get("source", "coverage_matrix"),
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
    pct = statistics.quantiles(lat, n=100) if len(lat) > 1 else lat * 100   # pct[49]=p50, pct[94]=p95
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
    """Answerable queries where hybrid fixed a dense failure. Two kinds:
      recovered : an expected article is in hybrid's top-k but absent from dense's top-k
      rank      : hybrid ranked the first expected article higher than dense did
    """
    wins = []
    for d, h in zip(dense_rows, hybrid_rows):
        if not d["answerable"]:
            continue
        item = items[d["query_id"]]
        expected = set(item["expected_articles"])
        recovered = sorted((expected & set(h["returned"])) - set(d["returned"]))
        rank_win = bool(h["first_rank"]) and (d["first_rank"] is None or h["first_rank"] < d["first_rank"])
        if recovered or rank_win:
            wins.append({"query_id": d["query_id"], "query": item["query"], "expected": item["expected_articles"],
                         "kind": "recovered" if recovered else "rank", "recovered": recovered,
                         "dense_rank": d["first_rank"], "hybrid_rank": h["first_rank"],
                         "dense_returned": d["returned"], "hybrid_returned": h["returned"]})
    return wins


def rankings(per_mode: dict) -> str:
    """Fingerprint of every ranking, to compare runs."""
    return json.dumps({m: [r["returned"] for r in rows] for m, rows in per_mode.items()})


# output report as MD 

def to_markdown(summary: dict, wins: list[dict], k: int, deterministic, budget: int, n_ans: int,
                by_source: dict | None = None) -> str:
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

    if by_source:
        out += ["", "### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)", "",
                "| source | mode | precision@k | recall@k | hit@k | MRR |", "|---|---|---|---|---|---|"]
        for src, per_mode_summary in by_source.items():
            for m in MODES:
                s = per_mode_summary[m]
                out.append(f"| {src} | {m} | {s['precision']:.3f} | {s['recall']:.3f} | {s['hit_rate']:.3f} | {s['mrr']:.3f} |")

    out += ["", "### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)", ""]
    if wins:
        out += ["| incident | query | expected | kind | article dense missed | dense rank | hybrid rank |",
                "|---|---|---|---|---|---|---|"]
        for w in wins:
            out.append(f"| {w['query_id']} | {w['query']} | {', '.join(w['expected'])} | {w['kind']} "
                       f"| {', '.join(w['recovered']) or '-'} | {w['dense_rank'] or 'miss'} | {w['hybrid_rank']} |")
    else:
        out.append("_none at this k_")

    if deterministic is not None:
        out += ["", f"### Determinism: two full runs produced **{'IDENTICAL' if deterministic else 'DIFFERENT'}** rankings in every mode"]
    return "\n".join(out) + "\n"


# S2.6 : do the extractors earn their place?
#
# A stressor row is graded on the section that answers it, not on a KB number.
# The manual is indexed twice into the shared collection and the question is
# whether the reading that keeps tables as tables, form labels attached to their
# values and columns in the right order finds sections the line reading misses.
# All three arms are searched with the same mode, the same k and the same
# collection, so the only thing that differs is which points are eligible.

def _search_arm(query: str, arm: str, mode: str, k: int, client) -> list:
    return search(query, mode=mode, top_k=k, client=client,
                  filters=MANUAL_ARMS[arm], collection=QDRANT.collection_name)


def run_stressor_arm(arm: str, items: list[dict], mode: str, k: int, client) -> list[dict]:
    _search_arm(items[0]["query"], arm, mode, k, client)   # warm-up, not timed
    rows = []
    for item in items:
        t0 = time.perf_counter()
        hits = _search_arm(item["query"], arm, mode, k, client)
        ms = (time.perf_counter() - t0) * 1000
        got = list(dict.fromkeys(h.section for h in hits))
        row = {
            "query_id": item["query_id"],
            "capability_class": item.get("capability_class", ""),
            "requires_extractor": item.get("requires_extractor", False),
            "answerable": item["answerable"],
            "ms": round(ms, 1),
            "top_score": round(hits[0].score, 4) if hits else None,
            "returned": got,
            "returners": [h.payload.get("extractor", "line_reading") for h in hits],
        }
        if item["answerable"]:
            row.update(grade(got, item["expected_sections"]))
            # Which reading actually produced the hit, so a win can be attributed
            # to the table extractor rather than to the line reading in the same arm.
            row["hit_from"] = [
                h.payload.get("extractor", "line_reading") for h in hits
                if h.section in set(item["expected_sections"])
            ]
        rows.append(row)
    return rows


def summarise_arm(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    lat = sorted(r["ms"] for r in rows)
    pct = statistics.quantiles(lat, n=100) if len(lat) > 1 else lat * 100
    out = {
        "precision": round(statistics.mean(r["precision"] for r in ans), 3) if ans else 0.0,
        "recall": round(statistics.mean(r["recall"] for r in ans), 3) if ans else 0.0,
        "hit_rate": round(statistics.mean(r["hit"] for r in ans), 3) if ans else 0.0,
        "mrr": round(statistics.mean(r["rr"] for r in ans), 3) if ans else 0.0,
        "answered_unanswerable": sum(bool(r["returned"]) for r in unans),
        "unanswerable": len(unans),
        "p50_ms": round(pct[49], 1),
        "p95_ms": round(pct[94], 1),
    }
    # Only rows that genuinely need the extractor count towards the verdict, so a
    # row answerable from plain prose cannot flatter the extractor arm.
    need = [r for r in ans if r["requires_extractor"]]
    if need:
        out["extractor_dependent_hit_rate"] = round(statistics.mean(r["hit"] for r in need), 3)
    return out


def arm_regressions(base: dict[str, dict], other: dict[str, dict]) -> list[dict]:
    """Rows the extractor arm answers that the other arm did not, and the reverse.

    Only answerable rows are compared.  An unanswerable row is scored by whether
    anything came back at all, which is a different question, and it has no
    ``hit`` to diff.

    ``query`` is read leniently: the caller merges it in from the evaluation set
    for the report, and the comparison works without it.
    """
    lost, gained = [], []
    for qid, b in base.items():
        if not b.get("answerable"):
            continue
        o = other[qid]
        if b["hit"] and not o["hit"]:
            lost.append({"query_id": qid, "capability_class": b["capability_class"],
                         "query": b.get("query", qid), "baseline_returned": b["returned"],
                         "extractor_returned": o["returned"]})
        elif o["hit"] and not b["hit"]:
            gained.append({"query_id": qid, "capability_class": o["capability_class"],
                           "query": o.get("query", qid), "baseline_returned": b["returned"],
                           "extractor_returned": o["returned"],
                           "hit_from": o.get("hit_from", [])})
    return gained + lost


def stressor_markdown(arm_rows: dict[str, list[dict]], items: dict, mode: str, k: int) -> str:
    summaries = {arm: summarise_arm(rows) for arm, rows in arm_rows.items()}
    base_by_id = {r["query_id"]: {**r, "query": items[r["query_id"]]["query"]}
                  for r in arm_rows["baseline"]}
    ext_by_id = {r["query_id"]: {**r, "query": items[r["query_id"]]["query"]}
                 for r in arm_rows["stressor"]}
    changes = arm_regressions(base_by_id, ext_by_id)

    out = [f"## Manual stressor ablation @k={k} — mode `{mode}`", "",
           "One collection (`" + QDRANT.collection_name + "`), three filters over it. The KB "
           "articles and the manual's extracted pages share a search space, so they compete for "
           "the same top-k. `baseline` = KB articles only, `stressor` = the manual's extracted "
           "pages only, `combined` = everything, which is what a live query gets.", "",
           "| arm | eligible points | precision@k | recall@k | hit@k | MRR | hit@k (extractor rows only) | "
           "answered-unanswerable | p95 ms |",
           "|---|---|---|---|---|---|---|---|---|"]
    for arm, s in summaries.items():
        eligible = ARM_ELIGIBILITY[arm]
        out.append(f"| {arm} | {eligible} | {s['precision']:.3f} | {s['recall']:.3f} "
                   f"| {s['hit_rate']:.3f} | {s['mrr']:.3f} "
                   f"| {s.get('extractor_dependent_hit_rate', 'n/a')} "
                   f"| {s['answered_unanswerable']}/{s['unanswerable']} | {s['p95_ms']} |")

    out += ["", "### By capability class (hit@k per arm)", "",
            "| capability | rows | baseline | stressor | delta |", "|---|---|---|---|---|"]
    classes = sorted({r["capability_class"] for rows in arm_rows.values() for r in rows if r["capability_class"]})
    for klass in classes:
        ids = {r["query_id"] for r in arm_rows["baseline"] if r["capability_class"] == klass}
        b = [base_by_id[i] for i in ids if base_by_id[i]["answerable"]]
        e = [ext_by_id[i] for i in ids if ext_by_id[i]["answerable"]]
        bh = statistics.mean(r["hit"] for r in b) if b else None
        eh = statistics.mean(r["hit"] for r in e) if e else None
        delta = f"{eh - bh:+.3f}" if bh is not None and eh is not None else "n/a"
        out.append(f"| {klass} | {len(ids)} | "
                   f"{'—' if bh is None else f'{bh:.3f}'} | "
                   f"{'—' if eh is None else f'{eh:.3f}'} | {delta} |")

    if changes:
        out += ["", "### What the extractor arm changed", "",
            "The `baseline` arm here is the KB articles, which carry no manual section labels, "
            "so a section-level row can only be answered by a manual point. That makes this table "
            "a reading of *whether the manual is in the corpus at all*, not a measure of extractor "
            "quality -- see the line-reading comparison at the end of this file for that.", "",
                "| incident | query | class | change | KB-only returned | extractor returned | hit produced by |",
                "|---|---|---|---|---|---|---|"]
        for change in changes:
            won = not base_by_id[change["query_id"]]["hit"]
            out.append(f"| {change['query_id']} | {change['query']} | {change['capability_class']} "
                       f"| {'gained' if won else 'LOST'} "
                       f"| {', '.join(change['baseline_returned']) or 'nothing'} "
                       f"| {', '.join(change['extractor_returned']) or 'nothing'} "
                       f"| {', '.join(change.get('hit_from', []) or ['—'])} |")
    else:
        out += ["", "### What the extractor arm changed", "",
            "The `baseline` arm here is the KB articles, which carry no manual section labels, "
            "so a section-level row can only be answered by a manual point. That makes this table "
            "a reading of *whether the manual is in the corpus at all*, not a measure of extractor "
            "quality -- see the line-reading comparison at the end of this file for that.", "", "_No hit changed hands at this k._"]

    out += ["", "An unanswerable row that returns anything is counted above rather than scored: the "
            "manual does not set a retention period, and a confident chunk about one is a wrong "
            "answer, not a weak hit."]
    return "\n".join(out) + "\n"


# our main

def run_stressors(data: dict, k: int, mode: str, client) -> dict:
    """The manual half of the set, run against both readings of the manual."""
    items = [i for i in data["items"] if i.get("source") == "manual_stressor"]
    if not items:
        print("no manual_stressor rows in the evaluation set")
        return {}
    by_id = {i["query_id"]: i for i in items}
    print(f"manual stressors v{data['version']}: {len(items)} rows, k={k}, mode={mode}\n")

    arm_rows = {}
    for arm in MANUAL_ARMS:
        arm_rows[arm] = run_stressor_arm(arm, items, mode, k, client)
        summary = summarise_arm(arm_rows[arm])
        print(f"arm {arm:9s} hit@k={summary['hit_rate']:.3f} "
              f"recall@k={summary['recall']:.3f} mrr={summary['mrr']:.3f} "
              f"p95={summary['p95_ms']}ms")

    summaries = {arm: summarise_arm(rows) for arm, rows in arm_rows.items()}
    base = {r["query_id"]: {**r, "query": by_id[r["query_id"]]["query"]} for r in arm_rows["baseline"]}
    ext = {r["query_id"]: {**r, "query": by_id[r["query_id"]]["query"]} for r in arm_rows["stressor"]}
    changes = arm_regressions(base, ext)
    gained = [c for c in changes if not base[c["query_id"]]["hit"]]
    lost = [c for c in changes if base[c["query_id"]]["hit"]]
    print(f"\nextractors gained {len(gained)}, lost {len(lost)} of {sum(i['answerable'] for i in items)} rows")

    md = stressor_markdown(arm_rows, by_id, mode, k)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"stressors_k{k}_{mode}.md").write_text(md, encoding="utf-8")
    (RESULTS / f"stressors_k{k}_{mode}.json").write_text(json.dumps({
        "evaluation_set_version": data["version"], "k": k, "mode": mode,
        # All three arms searched this one collection; MANUAL_ARMS holds filter
        # objects, so what goes in the artifact is what each arm made eligible.
        "collection": QDRANT.collection_name,
        "arms": {arm: ARM_ELIGIBILITY[arm] for arm in MANUAL_ARMS},
        "summary": summaries,
        "quality_fingerprint": quality_fingerprint(summaries),
        "gained": gained, "lost": lost, "per_query": arm_rows,
    }, indent=2), encoding="utf-8")
    print(f"wrote eval/results/stressors_k{k}_{mode}.{{md,json}}")
    return {"summary": summaries, "gained": gained, "lost": lost, "per_query": arm_rows}


def quality_fingerprint(summaries: dict[str, dict]) -> dict:
    """The numbers to compare between runs, with timing left out.

    ``embed_dense`` is one HTTPS round-trip per query, so p50 and p95 move by
    hundreds of milliseconds between two identical runs.  Everything else here
    is deterministic, and it is this block -- not the whole ``summary`` -- that a
    second run should reproduce exactly.
    """
    return {
        "note": "quality metrics only; timing excluded because embed_dense is a network round-trip",
        **{arm: {k: v for k, v in s.items() if not k.endswith("_ms")}
           for arm, s in summaries.items()},
    }


# --------------------------------------------------------------------------- #
# the S2.6 no-regression check, on the shared collection
# --------------------------------------------------------------------------- #
#
# The check this replaces scored the baseline against a copy of the manual in a
# collection of its own, where a baseline query could not see a stressor point at
# all. Every number came out unchanged no matter what the extractors produced.
#
# The real question is narrower and answerable: the manual's 48 extracted pages
# now sit in QDRANT.collection_name, so they take slots in the same top-k as the
# KB articles. Do the 37 baseline rows still find what they found?
#
# Both conditions are measured here in one pass, against one collection, with one
# piece of code. The only difference is the is_stressor filter, so the comparison
# cannot be confounded by run-to-run drift in the corpus or the index.
#
# A filtered search is the right instrument here: the filter is applied inside
# Qdrant before ranking, so `is_stressor=False` returns the same KB points, in the
# same order, that a collection holding only KB points would. --regression-drop
# re-measures after physically removing the points, to confirm that.

REGRESSION_CONDITIONS = {
    "kb_only": RetrievalFilters(is_stressor=False),   # the 37 rows, stressor points ineligible
    "mixed": None,                                    # the 37 rows, live conditions
}


def run_regression(data: dict, k: int, modes: list[str], client) -> dict:
    """Score the baseline rows with and without the manual's pages in the corpus."""
    items = [i for i in data["items"] if i.get("source") != "manual_stressor"]
    if not items:
        print("no baseline rows in the evaluation set")
        return {}
    by_id = {i["query_id"]: i for i in items}
    n_ans = sum(i["answerable"] for i in items)
    print(f"S2.6 regression @k={k}: {len(items)} baseline rows ({n_ans} answerable), "
          f"collection `{QDRANT.collection_name}`\n")

    rows, summaries, per_mode = {}, {}, {}
    for condition, flt in REGRESSION_CONDITIONS.items():
        for mode in modes:
            got = run_mode(mode, items, k, client, filters=flt)
            rows[(condition, mode)] = {r["query_id"]: r for r in got}
            summaries[f"{condition}|{mode}"] = summarise(got)
            s = summaries[f"{condition}|{mode}"]
            print(f"  {condition:8s} {mode:14s} P={s['precision']:.3f} R={s['recall']:.3f} "
                  f"hit={s['hit_rate']:.3f} MRR={s['mrr']:.3f}")

    # What changed, per mode. A stressor point carries number == "" (a manual
    # section has no KB number), so an empty string in a result list is a slot the
    # manual took away from the KB.
    verdicts, lost, gained = {}, {}, {}
    for mode in modes:
        b, m = rows[("kb_only", mode)], rows[("mixed", mode)]
        lost[mode], gained[mode], hist = [], [], collections.Counter()
        for qid, br in b.items():
            mr = m[qid]
            hist[sum(1 for n in mr["returned"] if n == "")] += 1
            if br.get("hit") and not mr.get("hit"):
                lost[mode].append({
                    "query_id": qid, "query": by_id[qid]["query"],
                    "expected": by_id[qid]["expected_articles"],
                    "kb_only_returned": br["returned"], "kb_only_first_rank": br["first_rank"],
                    "mixed_returned": mr["returned"],
                })
            elif mr.get("hit") and not br.get("hit"):
                gained[mode].append({"query_id": qid, "query": by_id[qid]["query"]})
        verdicts[mode] = {
            "hit_rate_kb_only": summaries[f"kb_only|{mode}"]["hit_rate"],
            "hit_rate_mixed": summaries[f"mixed|{mode}"]["hit_rate"],
            "mrr_kb_only": summaries[f"kb_only|{mode}"]["mrr"],
            "mrr_mixed": summaries[f"mixed|{mode}"]["mrr"],
            "hits_lost": len(lost[mode]), "hits_gained": len(gained[mode]),
            "regression": len(lost[mode]) > len(gained[mode]),
            "queries_where_a_stressor_took_a_slot": {
                str(slots): n for slots, n in sorted(hist.items()) if slots
            },
            "lost": lost[mode], "gained": gained[mode],
        }
        v = verdicts[mode]
        verdict = "REGRESSION" if v["regression"] else "held"
        print(f"  {mode:14s} hit {v['hit_rate_kb_only']:.3f} -> {v['hit_rate_mixed']:.3f}  "
              f"lost {v['hits_lost']}, gained {v['hits_gained']}  {verdict}")

    payload = {
        "k": k, "modes": modes, "collection": QDRANT.collection_name,
        "conditions": {name: ("is_stressor=False" if f else "no filter (live)")
                       for name, f in REGRESSION_CONDITIONS.items()},
        "baseline_rows": len(items), "answerable_rows": n_ans,
        "evaluation_set_version": data["version"],
        "summary": summaries, "verdict": verdicts,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"regression_k{k}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS / f"regression_k{k}.md").write_text(regression_markdown(payload), encoding="utf-8")
    print(f"\nwrote eval/results/regression_k{k}.{{md,json}}")
    return payload


def regression_markdown(p: dict) -> str:
    k, modes = p["k"], p["modes"]
    out = [f"## S2.6 no-regression check — baseline rows against the shared collection", "",
           f"`{p['collection']}` holds the KB articles *and* the manual's extracted pages, so "
           f"the two compete for the same {k} slots. "
           f"{p['baseline_rows']} baseline rows ({p['answerable_rows']} answerable), scored twice: "
           "`kb_only` filters the manual out, `mixed` is what a live query gets. Same collection, "
           "same code, same pass — the `is_stressor` filter is the only difference.", "",
           "| mode | hit@k (kb only) | hit@k (mixed) | Δ | MRR (kb only) | MRR (mixed) | "
           "hits lost | gained | verdict |",
           "|---|---|---|---|---|---|---|---|---|"]
    for mode in modes:
        v = p["verdict"][mode]
        delta = v["hit_rate_mixed"] - v["hit_rate_kb_only"]
        out.append(
            f"| `{mode}` | {v['hit_rate_kb_only']:.3f} | {v['hit_rate_mixed']:.3f} | "
            f"{delta:+.3f} | {v['mrr_kb_only']:.3f} | {v['mrr_mixed']:.3f} | "
            f"{v['hits_lost']} | {v['hits_gained']} | "
            f"{'**REGRESSION**' if v['regression'] else 'held'} |")
    out += ["", "### Where the manual took a slot", "",
            "A manual section has no KB number, so an empty string in a result list is a "
            "stressor point. Counted per baseline query:", "",
            "| mode | queries with 1 manual hit in top-k | queries with 2+ |", "|---|---|---|"]
    for mode in modes:
        h = p["verdict"][mode]["queries_where_a_stressor_took_a_slot"]
        one = h.get("1", 0)
        more = sum(v for s, v in h.items() if int(s) > 1)
        out.append(f"| `{mode}` | {one} | {more} |")
    for mode in modes:
        for row in p["verdict"][mode]["lost"]:
            # extend, not += : a trailing comma after the ] would make this a
            # 1-tuple, and `out += ([...],)` appends the list as a single element.
            out.extend([
                "", f"#### `{row['query_id']}` — {row['query']}", "",
                f"- expects: `{row['expected']}`",
                f"- kb only (first hit at rank {row['kb_only_first_rank']}): `{row['kb_only_returned']}`",
                f"- mixed: `{row['mixed_returned']}`  ← `''` is a manual page",
            ])
    return "\n".join(out) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=RETRIEVAL.top_k)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--plant", action="store_true")
    ap.add_argument("--unplant", action="store_true")
    ap.add_argument("--stressors", action="store_true",
                    help="run the manual stressor rows against both readings of the manual")
    ap.add_argument("--stressor-mode", default="hybrid", choices=MODES,
                    help="retrieval mode for the stressor arms (default: hybrid)")
    ap.add_argument("--regression", action="store_true",
                    help="score the baseline rows with and without the manual's extracted "
                         "pages in the shared collection (S2.6 no-regression check)")
    ap.add_argument("--regression-modes", default=",".join(MODES),
                    help="modes for --regression (default: all)")
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

    if args.stressors:
        run_stressors(data, args.k, args.stressor_mode, client)
        return

    if args.regression:
        run_regression(data, args.k, [m.strip() for m in args.regression_modes.split(",")], client)
        return

    # The stressor rows carry expected_sections, not expected_articles. Running
    # them through run_mode would grade every one of them against an empty
    # expected set and score each as a miss no matter what came back.
    kb_items = [i for i in items if i.get("source") != "manual_stressor"]
    skipped = len(items) - len(kb_items)
    if skipped:
        print(f"(skipping {skipped} manual_stressor rows; run with --stressors for those)\n")

    print(f"evaluation set v{data['version']}: {len(kb_items)} incidents, k={args.k}, runs={args.runs}\n")

    fingerprints, per_mode = [], {}
    for run in range(1, args.runs + 1):
        per_mode = {m: run_mode(m, kb_items, args.k, client) for m in MODES}
        fingerprints.append(rankings(per_mode))
        for m in MODES:
            print(f"run {run}  {m:14s} {summarise(per_mode[m])}")
    deterministic = all(f == fingerprints[0] for f in fingerprints) if args.runs > 1 else None

    summary = {m: summarise(rows) for m, rows in per_mode.items()}
    sources = list(dict.fromkeys(i.get("source", "coverage_matrix") for i in kb_items))
    by_source = {src: {m: summarise([r for r in rows if r["source"] == src]) for m, rows in per_mode.items()}
                 for src in sources} if len(sources) > 1 else None
    wins = sparse_wins(per_mode["dense"], per_mode["hybrid"], by_id)
    n_ans = sum(i["answerable"] for i in kb_items)
    md = to_markdown(summary, wins, args.k, deterministic, RETRIEVAL.latency_budget_ms, n_ans, by_source)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"ablation_k{args.k}.md").write_text(md, encoding="utf-8")
    (RESULTS / f"ablation_k{args.k}.json").write_text(json.dumps({
        "evaluation_set_version": data["version"], "k": args.k, "runs": args.runs,
        "deterministic": deterministic, "summary": summary, "by_source": by_source, "sparse_wins": wins, "per_query": per_mode,
    }, indent=2), encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
