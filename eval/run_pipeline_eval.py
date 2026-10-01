from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

# Allow imports when executed as:
# py eval\run_pipeline_eval.py
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.adapters import DATA
from src.agent.nodes.retrieve import retrieve_node
from src.agent.nodes.diagnose import diagnose_node
from src.agent.nodes.generate import generate_node


def _extract_answer(result: dict[str, Any]) -> str:
    outputs = result.get("outputs") or {}

    # Normal generated resolution
    resolution = outputs.get("resolution")
    if resolution:
        return str(resolution)

    # Cached KBHR resolution
    cached = result.get("cached_resolution")
    if cached:
        return str(cached)

    # Refusal/escalation path
    failure_reason = result.get("failure_reason")
    if failure_reason:
        return str(failure_reason)

    return ""


def _extract_retrieved(result: dict[str, Any]) -> list[str]:
    evidence = result.get("retrieved_evidence") or []

    return [
        str(item.get("text", ""))
        for item in evidence
        if item.get("text")
    ]


def _extract_retrieved_ids(result: dict[str, Any]) -> list[str]:
    evidence = result.get("retrieved_evidence") or []

    return [
        str(item.get("section_id") or item.get("section") or item.get("id", ""))
        for item in evidence
        if item.get("section_id") or item.get("section") or item.get("id")
    ]


def _build_eval_state(question: str) -> dict[str, Any]:
    """Build the minimal state required by the RAG evaluation path."""
    execution_id = f"eval-{uuid.uuid4().hex}"
    incident_number = f"EVAL-{uuid.uuid4().hex[:12].upper()}"

    return {
        "execution_id": execution_id,
        "incident_number": incident_number,
        "incident_payload": {
            "number": incident_number,
            "short_description": question,
            "description": question,
        },
        "outputs": {},
    }


def _run_rag_pipeline(question: str) -> dict[str, Any]:
    """
    Evaluation-only RAG pipeline.

    This intentionally evaluates the existing retrieval and generation
    components directly:

        retrieve -> diagnose -> generate

    It does NOT execute the production risk/classification/interrupt path.
    Production graph behavior is therefore left unchanged.
    """
    state = _build_eval_state(question)

    try:
        # ---------------------------------------------------------
        # 1. Retrieval
        # ---------------------------------------------------------
        retrieval_result = retrieve_node(state)
        state.update(retrieval_result)

        evidence = state.get("retrieved_evidence") or []

        if not evidence:
            return {
                "answer": "",
                "retrieved_contexts": [],
                "retrieved_ids": [],
                "retrieval_cache_hit": False,
                "error": None,
            }

        # ---------------------------------------------------------
        # 2. KBHR cache-hit path
        # ---------------------------------------------------------
        # This mirrors the existing retrieve_node behavior.
        # If a strong human-approved KB article was found, it already
        # contains the resolution and production would reuse it.
        if state.get("retrieval_cache_hit"):
            return {
                "answer": _extract_answer(state),
                "retrieved_contexts": _extract_retrieved(state),
                "retrieved_ids": _extract_retrieved_ids(state),
                "retrieval_cache_hit": True,
                "error": None,
            }

        # ---------------------------------------------------------
        # 3. Diagnosis
        # ---------------------------------------------------------
        diagnosis_result = diagnose_node(state)
        state.update(diagnosis_result)

        # ---------------------------------------------------------
        # 4. Resolution generation
        # ---------------------------------------------------------
        generation_result = generate_node(state)
        state.update(generation_result)

        return {
            "answer": _extract_answer(state),
            "retrieved_contexts": _extract_retrieved(state),
            "retrieved_ids": _extract_retrieved_ids(state),
            "retrieval_cache_hit": False,
            "error": None,
        }

    except Exception as exc:
        print(
            f"[ERROR] RAG evaluation failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        return {
            "answer": "",
            "retrieved_contexts": [],
            "retrieved_ids": [],
            "retrieval_cache_hit": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_agent(
    question: str,
    history: list[dict],
) -> tuple[str, list[str]]:
    """
    Adapter required by eval.adapters.

    Returns:
        (generated_answer, retrieved_contexts)
    """
    # The current production pipeline does not implement query rewriting,
    # so history is intentionally not injected into the query.
    del history

    result = _run_rag_pipeline(question)

    return (
        result["answer"],
        result["retrieved_contexts"],
    )


def run_one(
    question: str,
    history: list[dict],
) -> dict[str, Any]:
    """
    Execute the RAG evaluation path while preserving retrieval IDs and
    metadata needed by deterministic retrieval scoring.
    """
    del history

    return _run_rag_pipeline(question)


def run_dataset(standalone: bool = False) -> list[dict[str, Any]]:
    results = []

    for session in DATA["sessions"]:
        history: list[dict] = []

        for turn in session["turns"]:
            question = (
                turn["standalone_input"]
                if standalone
                else turn["input"]
            )

            print(
                f"[{turn['turn_id']}] "
                f"{turn['expected_behaviour']}: "
                f"{question[:100]}"
            )

            result = run_one(question, history)

            row = {
                "session_id": session["session_id"],
                "turn_id": turn["turn_id"],
                "input": turn["input"],
                "standalone_input": turn["standalone_input"],
                "question_used": question,
                "reference": turn["reference"],
                "reference_contexts": turn["reference_contexts"],
                "expected_sections": turn["expected_sections"],
                "must_not_retrieve": turn["must_not_retrieve"],
                "expected_behaviour": turn["expected_behaviour"],
                "difficulty": turn["difficulty"],
                "requires": turn["requires"],
                "tags": turn["tags"],
                **result,
            }

            results.append(row)

            history.append(
                {
                    "role": "user",
                    "content": turn["input"],
                }
            )

            history.append(
                {
                    "role": "assistant",
                    "content": result["answer"],
                }
            )

    return results


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Use standalone_input instead of input.",
    )

    parser.add_argument(
        "--output",
        default="eval/results/full_pipeline.json",
    )

    args = parser.parse_args()

    results = run_dataset(
        standalone=args.standalone,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as f:
        json.dump(
            results,
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    print()
    print(f"Saved {len(results)} results to {output}")


if __name__ == "__main__":
    main()