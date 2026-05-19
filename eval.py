import argparse
import json
import re
import statistics
import time
from typing import Any, Dict, Iterable, List, Tuple

from app.workflow.graph import agentic_graph

RISK_HINT = "\u98ce\u9669\u63d0\u793a"


def iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def extract_answer(text: str) -> str:
    if not text:
        return ""
    if "<answer>" in text and "</answer>" in text:
        start = text.find("<answer>") + len("<answer>")
        end = text.find("</answer>")
        return text[start:end].strip()
    return text.strip()


def normalize_answer(text: str) -> str:
    text = extract_answer(text)
    if not text:
        return ""
    # Drop disclaimer lines.
    lines = [line for line in text.splitlines() if RISK_HINT not in line]
    text = "\n".join(lines)
    # Remove citation markers like [1].
    text = re.sub(r"\[\d+\]", "", text)
    # Remove whitespace.
    text = re.sub(r"\s+", "", text)
    return text


def estimate_tool_calls(result: Dict[str, Any]) -> int:
    # Approximate external tool calls as search requests.
    if not result.get("should_search_web", True):
        return 0
    calls = 1  # initial search
    pipeline_id = int(result.get("pipeline_id", 2))
    fallback = bool(result.get("fallback_to_pipeline_2", False))
    if pipeline_id in (2, 3) or fallback:
        calls += len(result.get("queries", []) or [])
    return calls


def evaluate_item(item: Dict[str, Any], model: str | None) -> Tuple[bool, float, int]:
    question = (item.get("question") or "").strip()
    expected = item.get("answer") or ""

    start = time.perf_counter()
    result = agentic_graph.invoke({"question": question, "model": model})
    elapsed = time.perf_counter() - start

    predicted = result.get("answer", "")
    is_correct = normalize_answer(predicted) == normalize_answer(expected)
    tool_calls = estimate_tool_calls(result)
    return is_correct, elapsed, tool_calls


def summarize(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    values_sorted = sorted(values)
    p50 = values_sorted[int(0.50 * (len(values_sorted) - 1))]
    p95 = values_sorted[int(0.95 * (len(values_sorted) - 1))]
    return {
        "avg": sum(values_sorted) / len(values_sorted),
        "p50": p50,
        "p95": p95,
        "max": max(values_sorted),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate eval.jsonl with AgenticSearch")
    parser.add_argument("--data", default="eval.jsonl", help="Path to eval.jsonl")
    parser.add_argument("--model", default=None, help="Override model name")
    parser.add_argument("--limit", type=int, default=0, help="Max samples (0 for all)")
    parser.add_argument("--verbose", action="store_true", help="Print per-item results")
    args = parser.parse_args()

    latencies: List[float] = []
    tool_calls_list: List[int] = []
    correct = 0
    total = 0

    for item in iter_jsonl(args.data):
        if args.limit and total >= args.limit:
            break
        ok, elapsed, tool_calls = evaluate_item(item, args.model)
        total += 1
        correct += 1 if ok else 0
        latencies.append(elapsed)
        tool_calls_list.append(tool_calls)
        if args.verbose:
            print(f"{item.get('id', '')}\tok={ok}\tsec={elapsed:.3f}\ttools={tool_calls}")

    accuracy = (correct / total) if total else 0.0
    latency_stats = summarize(latencies)
    tool_avg = (sum(tool_calls_list) / total) if total else 0.0

    print("samples", total)
    print(f"accuracy {accuracy:.4f}")
    print(
        "latency_sec "
        f"avg={latency_stats['avg']:.3f} "
        f"p50={latency_stats['p50']:.3f} "
        f"p95={latency_stats['p95']:.3f} "
        f"max={latency_stats['max']:.3f}"
    )
    print(f"tool_calls_avg {tool_avg:.2f}")


if __name__ == "__main__":
    main()
