from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv

from deepeval.models.base_model import DeepEvalBaseLLM


load_dotenv()


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from eval.adapters import DATA, turns, score_retrieval
from src.retrieval.hybrid_search import search


RESULTS_DIR = ROOT / "eval" / "results"
REPORT = ROOT / "eval" / "regression_report.md"


# ============================================================
# DeepEval custom judge
# ============================================================

class ProjectGeminiJudge(DeepEvalBaseLLM):
    """
    DeepEval adapter around the project's existing LLM.

    The application already configures Gemini through:

        src.agent.llm.get_llm()

    This avoids DeepEval's LiteLLMModel path, which was trying to
    download the OpenAI cl100k tokenizer from the public internet.
    """

    def __init__(self):
        from src.agent.llm import get_llm

        self.model = get_llm()

    def load_model(self):
        return self.model

    @staticmethod
    def _extract_content(response) -> str:
        """
        Convert a LangChain AIMessage into plain text.
        """

        content = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )

        # Gemini/LangChain can sometimes return structured content blocks.
        if isinstance(content, list):
            parts = []

            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")

                    if text is not None:
                        parts.append(str(text))
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))

            content = "\n".join(parts)

        return str(content)

    @staticmethod
    def _parse_schema(content: str, schema):
        """
        Convert the model's JSON output into the Pydantic schema
        DeepEval supplied.
        """

        text = str(content).strip()

        # Remove Markdown JSON fences if Gemini adds them.
        if text.startswith("```"):
            lines = text.splitlines()

            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            text = "\n".join(lines).strip()

        # First attempt: normal JSON parsing.
        try:
            data = json.loads(text)

        except json.JSONDecodeError:
            # Sometimes the model puts explanatory text around JSON.
            start = text.find("{")
            end = text.rfind("}")

            if start == -1 or end == -1 or end <= start:
                raise ValueError(
                    "DeepEval judge returned non-JSON output when "
                    "structured output was required:\n"
                    f"{text}"
                )

            data = json.loads(text[start:end + 1])

        # Pydantic v2
        if hasattr(schema, "model_validate"):
            return schema.model_validate(data)

        # Pydantic v1
        return schema(**data)

    def generate(self, prompt: str, schema=None):
        """
        Synchronous DeepEval generation.
        """

        model = self.load_model()

        response = model.invoke(prompt)

        content = self._extract_content(response)

        if schema is not None:
            return self._parse_schema(content, schema)

        return content

    async def a_generate(self, prompt: str, schema=None):
        """
        Asynchronous DeepEval generation.

        DeepEval uses this path when async_mode=True.
        """

        model = self.load_model()

        response = await model.ainvoke(prompt)

        content = self._extract_content(response)

        if schema is not None:
            return self._parse_schema(content, schema)

        return content

    def get_model_name(self):
        return "Project Gemini via existing application LLM"


# ============================================================
# RAG evaluation
# ============================================================

def run_rag_evaluation():
    """
    Deterministic retrieval evaluation over the existing golden set.
    """

    results = []

    all_turns = turns()

    for t in all_turns:
        try:
            chunks = search(
                query=t["standalone_input"],
                collection="barq_manual",
            )

            retrieved_ids = [
                str(
                    c.payload.get("section_label")
                    or c.payload.get("section_id")
                    or c.number
                    or c.point_id
                )
                for c in chunks
            ]

            score = score_retrieval(
                retrieved_ids,
                t,
            )

            results.append(score)

        except Exception as exc:
            results.append(
                {
                    "turn_id": t["turn_id"],
                    "recall": 0.0,
                    "precision": 0.0,
                    "top_k_contains_all": False,
                    "forbidden_retrieved": [],
                    "clean": False,
                    "error": str(exc),
                }
            )

    successful = [
        r
        for r in results
        if "error" not in r
    ]

    recall = (
        mean(
            [
                (r["recall"] or 0.0)
                for r in successful
            ]
        )
        if successful
        else 0.0
    )

    precision = (
        mean(
            [
                r["precision"]
                for r in successful
            ]
        )
        if successful
        else 0.0
    )

    hit_rate = (
        mean(
            [
                bool(r["top_k_contains_all"])
                for r in successful
            ]
        )
        if successful
        else 0.0
    )

    clean_rate = (
        mean(
            [
                bool(r["clean"])
                for r in successful
            ]
        )
        if successful
        else 0.0
    )

    return {
        "total": len(results),
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "hit_rate": round(hit_rate, 4),
        "clean_rate": round(clean_rate, 4),
        "details": results,
    }


# ============================================================
# Agent evaluation
# ============================================================

def run_agent_evaluation(limit: int | None = None):
    """
    Run actual agent outputs through the existing evaluation adapter.

    This is intentionally limited during development because it
    requires live LLM calls.
    """

    from eval.run_pipeline_eval import run_agent

    cases = []

    count = 0

    for session in DATA["sessions"]:
        history = []

        for turn in session["turns"]:

            if limit is not None and count >= limit:
                return cases

            question = turn["input"]

            try:
                answer, retrieved = run_agent(
                    question,
                    history,
                )

                cases.append(
                    {
                        "turn_id": turn["turn_id"],
                        "input": question,
                        "reference": turn["reference"],
                        "reference_contexts": turn["reference_contexts"],
                        "expected_behaviour": turn["expected_behaviour"],
                        "actual_output": answer,
                        "retrieval_context": retrieved,
                    }
                )

                history.append(
                    {
                        "role": "user",
                        "content": question,
                    }
                )

                history.append(
                    {
                        "role": "assistant",
                        "content": answer,
                    }
                )

                count += 1

            except Exception as exc:
                cases.append(
                    {
                        "turn_id": turn["turn_id"],
                        "input": question,
                        "reference": turn["reference"],
                        "actual_output": "",
                        "retrieval_context": [],
                        "error": str(exc),
                    }
                )

                count += 1

    return cases


# ============================================================
# DeepEval
# ============================================================

def run_deepeval(cases):
    """
    Run DeepEval quality metrics using the project's existing
    Gemini/LangChain LLM.
    """

    if not cases:
        return {
            "status": "SKIPPED",
            "reason": "No agent cases available",
        }

    from deepeval import evaluate

    from deepeval.metrics import (
        FaithfulnessMetric,
        AnswerRelevancyMetric,
        ContextualPrecisionMetric,
        ContextualRecallMetric,
    )

    from deepeval.test_case import LLMTestCase

    test_cases = []

    for case in cases:

        if case.get("error"):
            continue

        test_cases.append(
            LLMTestCase(
                input=case["input"],
                actual_output=case["actual_output"],
                expected_output=case["reference"],
                retrieval_context=case["retrieval_context"],
                context=case["reference_contexts"] or None,
            )
        )

    if not test_cases:
        return {
            "status": "FAILED",
            "reason": "No valid agent cases available for DeepEval",
        }

    # --------------------------------------------------------
    # Use the SAME LLM configuration as the application.
    # --------------------------------------------------------

    judge_model = ProjectGeminiJudge()

    print(
        "DeepEval judge model: "
        "Project Gemini via existing application LLM"
    )

    metrics = [
        FaithfulnessMetric(
            threshold=0.7,
            model=judge_model,
        ),

        AnswerRelevancyMetric(
            threshold=0.7,
            model=judge_model,
        ),

        ContextualPrecisionMetric(
            threshold=0.7,
            model=judge_model,
        ),

        ContextualRecallMetric(
            threshold=0.7,
            model=judge_model,
        ),
    ]

    print(
        f"Running DeepEval on "
        f"{len(test_cases)} test cases..."
    )

    try:
        result = evaluate(
            test_cases=test_cases,
            metrics=metrics,
        )

    except Exception as exc:
        return {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
            "cases": len(test_cases),
        }

    return {
        "status": "COMPLETED",
        "result": str(result),
        "cases": len(test_cases),
    }


# ============================================================
# Report
# ============================================================

def write_report(
    rag,
    agent,
    deepeval_result,
):
    lines = [
        "# Sprint 4 Regression Report",
        "",

        "## RAG Evaluation",
        "",

        "| Metric | Score |",
        "|---|---:|",

        f"| Mean Recall | {rag['recall']:.4f} |",
        f"| Mean Precision | {rag['precision']:.4f} |",
        f"| Hit Rate | {rag['hit_rate']:.4f} |",
        f"| Clean Rate | {rag['clean_rate']:.4f} |",

        "",

        f"Evaluated turns: **{rag['total']}**",
        f"Successful turns: **{rag['successful']}**",
        f"Failed turns: **{rag['failed']}**",

        "",

        "## Agent Evaluation",
        "",

        f"Cases executed: **{len(agent)}**",

        "",

        "## DeepEval",
        "",

        f"Status: **{deepeval_result['status']}**",
        "",

        "```text",
        deepeval_result.get("result", ""),
        "```",

        "",

        "## Baseline",
        "",

        "The RAG baseline was produced from the existing "
        "`barq_rag_eval_dataset.json` golden set and the "
        "`barq_manual` Qdrant collection.",

        "",
    ]

    REPORT.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--agent-limit",
        type=int,
        default=0,
        help=(
            "Number of agent cases to execute. "
            "0 disables agent evaluation."
        ),
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # RAG
    # --------------------------------------------------------

    print("Running RAG evaluation...")

    rag = run_rag_evaluation()

    print(
        f"RAG: recall={rag['recall']}, "
        f"precision={rag['precision']}, "
        f"hit_rate={rag['hit_rate']}, "
        f"clean_rate={rag['clean_rate']}"
    )

    # --------------------------------------------------------
    # Agent
    # --------------------------------------------------------

    agent = []

    if args.agent_limit > 0:

        print(
            f"Running agent evaluation for "
            f"{args.agent_limit} cases..."
        )

        agent = run_agent_evaluation(
            limit=args.agent_limit
        )

    # --------------------------------------------------------
    # DeepEval
    # --------------------------------------------------------

    deepeval_result = {
        "status": "SKIPPED",
        "reason": "Agent evaluation disabled",
    }

    if agent:

        print("Running DeepEval...")

        deepeval_result = run_deepeval(
            agent
        )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    write_report(
        rag,
        agent,
        deepeval_result,
    )

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        RESULTS_DIR / "deepeval_suite.json"
    ).write_text(
        json.dumps(
            {
                "rag": rag,
                "agent_cases": agent,
                "deepeval": deepeval_result,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()