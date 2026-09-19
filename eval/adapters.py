"""
Load barq_rag_eval_dataset.json into DeepEval or RAGAS.

The dataset is framework-neutral. This file is the thin layer that turns it into
whatever your evaluation harness expects, plus a per-turn retrieval scorer that
does not need an LLM judge at all.

    python adapters.py --list-capabilities
    python adapters.py --slice image_ocr
"""
from __future__ import annotations
import json, argparse, collections
from pathlib import Path
from typing import Callable, Iterable

# Resolve the dataset next to this file so it works from any working directory
DATA = json.load(open(Path(__file__).parent / "barq_rag_eval_dataset.json", encoding="utf-8"))


# ──────────────────────────────────────────────────────────── selection
def turns(where: Callable[[dict], bool] | None = None) -> list[dict]:
    out = []
    for s in DATA["sessions"]:
        for t in s["turns"]:
            t = {**t, "_session": {k: v for k, v in s.items() if k != "turns"}}
            if where is None or where(t):
                out.append(t)
    return out


def by_capability(cap: str) -> list[dict]:
    return turns(lambda t: cap in t["requires"])


def answerable() -> list[dict]:
    return turns(lambda t: t["expected_behaviour"] == "answer")


def negatives() -> list[dict]:
    return turns(lambda t: t["expected_behaviour"] in ("refuse", "clarify"))


# ──────────────────────────────────────────────────────────── DeepEval
def to_deepeval_cases(run, standalone: bool = False):
    """run(question:str, history:list[dict]) -> (answer:str, retrieved:list[str])"""
    from deepeval.test_case import LLMTestCase

    cases = []
    for s in DATA["sessions"]:
        history = []
        for t in s["turns"]:
            q = t["standalone_input"] if standalone else t["input"]
            answer, retrieved = run(q, history)
            history += [{"role": "user", "content": t["input"]},
                        {"role": "assistant", "content": answer}]
            cases.append(LLMTestCase(
                input=q,
                actual_output=answer,
                expected_output=t["reference"],
                retrieval_context=retrieved,
                context=t["reference_contexts"] or None,
                additional_metadata={
                    "turn_id": t["turn_id"], "session_id": s["session_id"],
                    "behaviour": t["expected_behaviour"], "difficulty": t["difficulty"],
                    "requires": t["requires"], "tags": t["tags"],
                    "expected_sections": t["expected_sections"],
                    "must_not_retrieve": t["must_not_retrieve"],
                    "geval_criteria": t["geval_criteria"],
                },
            ))
    return cases


def to_deepeval_conversations(run):
    """One ConversationalTestCase per session, for multi-turn metrics."""
    from deepeval.test_case import ConversationalTestCase, Turn

    convos = []
    for s in DATA["sessions"]:
        history, turns_ = [], []
        for t in s["turns"]:
            answer, _ = run(t["input"], history)
            history += [{"role": "user", "content": t["input"]},
                        {"role": "assistant", "content": answer}]
            turns_ += [Turn(role="user", content=t["input"]),
                       Turn(role="assistant", content=answer)]
        convos.append(ConversationalTestCase(
            turns=turns_,
            scenario=s["scenario"],
            expected_outcome=s["expected_outcome"],
            user_description=s["user_description"],
            additional_metadata={"session_id": s["session_id"], "title": s["title"]},
        ))
    return convos


def geval_metrics():
    """One GEval metric per global rubric, plus per-turn rubrics where present."""
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams as P

    rub = DATA["metric_suite"]["geval_global_rubrics"]
    params = [P.INPUT, P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT, P.RETRIEVAL_CONTEXT]
    return {name: GEval(name=name, criteria=text, evaluation_params=params, threshold=0.7)
            for name, text in rub.items()}


# ──────────────────────────────────────────────────────────── RAGAS
def to_ragas(run, standalone: bool = True):
    """Returns a list of dicts ready for ragas.EvaluationDataset.from_list."""
    rows = []
    for s in DATA["sessions"]:
        history = []
        for t in s["turns"]:
            q = t["standalone_input"] if standalone else t["input"]
            answer, retrieved = run(q, history)
            history += [{"role": "user", "content": t["input"]},
                        {"role": "assistant", "content": answer}]
            rows.append({
                "user_input": q,
                "response": answer,
                "retrieved_contexts": retrieved,
                "reference": t["reference"],
                "reference_contexts": t["reference_contexts"],
            })
    return rows


def to_ragas_multiturn(run):
    """Multi-turn form: one sample per session with the full message list."""
    from ragas.messages import HumanMessage, AIMessage

    samples = []
    for s in DATA["sessions"]:
        history, msgs = [], []
        for t in s["turns"]:
            answer, _ = run(t["input"], history)
            history += [{"role": "user", "content": t["input"]},
                        {"role": "assistant", "content": answer}]
            msgs += [HumanMessage(content=t["input"]), AIMessage(content=answer)]
        samples.append({"user_input": msgs,
                        "reference": s["expected_outcome"],
                        "metadata": {"session_id": s["session_id"]}})
    return samples


# ──────────────────────────────────────────────────── retrieval scoring
def score_retrieval(retrieved_sections: Iterable[str], t: dict) -> dict:
    """Deterministic, no judge required. Feed it the section ids your retriever returned."""
    got = set(retrieved_sections)
    want = set(t["expected_sections"]) - {"—"}
    forbidden = set(t["must_not_retrieve"])
    hit = want & got
    return {
        "turn_id": t["turn_id"],
        "recall": len(hit) / len(want) if want else None,
        "precision": len(hit) / len(got) if got else 0.0,
        "top_k_contains_all": want.issubset(got) if want else None,
        "forbidden_retrieved": sorted(forbidden & got),
        "clean": not (forbidden & got),
    }


# ──────────────────────────────────────────────────────────── reporting
def slice_report(results: list[dict]):
    """results: [{turn_id, passed: bool}, ...]  → pass rate per capability tag."""
    index = {t["turn_id"]: t for t in turns()}
    agg = collections.defaultdict(lambda: [0, 0])
    for r in results:
        t = index.get(r["turn_id"])
        if not t:
            continue
        for cap in t["requires"] + [f"difficulty:{t['difficulty']}",
                                    f"behaviour:{t['expected_behaviour']}"]:
            agg[cap][1] += 1
            agg[cap][0] += bool(r["passed"])
    return {k: {"passed": v[0], "total": v[1], "rate": round(v[0] / v[1], 3)}
            for k, v in sorted(agg.items())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-capabilities", action="store_true")
    ap.add_argument("--slice")
    a = ap.parse_args()
    if a.list_capabilities:
        c = collections.Counter(cap for t in turns() for cap in t["requires"])
        for cap, n in c.most_common():
            print(f"{n:3d}  {cap}")
    elif a.slice:
        for t in by_capability(a.slice):
            print(f"{t['turn_id']}  [{t['difficulty']}]  {t['input'][:80]}")
    else:
        d = DATA["dataset"]["counts"]
        print(f"{d['sessions']} sessions, {d['turns']} turns")
        print("behaviour:", d["by_behaviour"])
        print("difficulty:", d["by_difficulty"])
