# Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Count saved benchmark context with Anthropic. This sends public benchmark text.

Use --inspect to verify the saved inputs and write their counts and character
means without making requests. Supply both benchmark checkout paths explicitly.
Only point the script at trusted checkouts, since it loads their benchmark
renderers.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import runpy
import statistics
import sys


FIGURES = {
    "locomo-window": ("recall", "locomo", "plainwin", 1540),
    "locomo-antechamber": ("recall", "locomo", "chats", 1540),
    "lme-window": ("recall", "lme", "plainwin", 500),
    "lme-antechamber": ("recall", "lme", "chats", 500),
    "locomo-chats-only": ("answer", "locomo", "none", 600),
    "locomo-generic-notes": ("answer", "locomo", "generic", 600),
    "locomo-notes-people": ("answer", "locomo", "ours_people", 600),
}
MODEL = "claude-sonnet-5-5"
ENDPOINT = "/v1/messages/count_tokens"
API_VERSION = "2026-10-21"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def by_id(rows, expected):
    indexed = {row["id"]: row for row in rows}
    if len(rows) != expected or len(indexed) != expected:
        raise ValueError(f"expected {expected} distinct questions, found {len(indexed)}")
    return indexed


def saved_texts(spec, locomo_root, recall_root):
    stage, benchmark, arm, expected = spec
    if stage == "recall":
        # The published character calculation uses all saved rows, even rows
        # subsequently excluded from accuracy. It counts raw response fields.
        root = recall_root / "runs/v4.4.3"
        suffix = Path(arm) / "qwen/full" / f"{benchmark}.json"
        recall = by_id(read_json(root / "recall" / suffix)["questions"], expected)
        score = read_json(root / "score" / suffix)
        scored = by_id(score["questions"], expected)
        if recall.keys() != scored.keys() or any(
            row["chars"] != scored[qid]["chars"] or row.get("error")
            for qid, row in recall.items()
        ):
            raise ValueError("saved recall and score records disagree")
        chars = statistics.mean(row["chars"] for row in recall.values())
        if chars != score["mean_chars"]:
            raise ValueError("saved recall character mean differs from the score")
        render = runpy.run_path(str(recall_root / "acbench/render.py"))["render"]
        return {qid: render(row["raw"]) for qid, row in recall.items()}, chars

    root = locomo_root / "v3/runs/v4.4.0"
    # The three printed means are from Qwen's 600-question validation set.
    contexts = by_id(read_json(root / f"context/{arm}/validation/{benchmark}.json")["questions"], expected)
    answers = by_id(
        [read_json(path) for path in sorted((root / f"answer/{arm}/qwen/validation/{benchmark}").glob("*.json"))],
        expected,
    )
    if contexts.keys() != answers.keys():
        raise ValueError("saved answers and contexts cover different questions")
    render = runpy.run_path(str(locomo_root / "v3/render.py"))["render"]
    texts = {}
    for qid, row in contexts.items():
        context = render(row["raw"])
        digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
        answer = answers[qid]
        if (digest != row["context_sha256"] or digest != answer["context_sha256"]
                or len(context) != answer["context_chars"]):
            raise ValueError("rendered context differs from a saved reader input")
        texts[qid] = context
    return texts, statistics.mean(answer["context_chars"] for answer in answers.values())


def count_tokens(connection, key, text):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": text}]}).encode("utf-8")
    connection.request("POST", ENDPOINT, body, {
        "content-type": "application/json",
        "anthropic-version": API_VERSION,
        "x-api-key": key,
    })
    response = connection.getresponse()
    payload = response.read()
    if response.status != 200:
        raise ValueError(f"Anthropic returned HTTP {response.status}")
    data = json.loads(payload)
    count = data.get("input_tokens") if isinstance(data, dict) else None
    if type(count) is not int or count < 0:
        raise ValueError("Anthropic returned an invalid input token count")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locomo-root", type=Path, required=True, help="path to the locomo-bench checkout")
    parser.add_argument("--recall-root", type=Path, required=True, help="path to the antechamber-benchmark checkout")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inspect", action="store_true", help="verify inputs without sending any requests")
    args = parser.parse_args()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not args.inspect and not key:
        parser.error("set ANTHROPIC_API_KEY before counting tokens")
    results = {}
    inputs = {}
    cohorts = {}
    mismatched = set()
    for figure, spec in FIGURES.items():
        try:
            inputs[figure] = saved_texts(spec, args.locomo_root.expanduser(), args.recall_root.expanduser())
        except (OSError, KeyError, TypeError, ValueError) as error:
            results[figure] = {"status": "blocked", "mean_tokens": None, "reason": str(error)}
            continue
        cohort = spec[:2]
        ids = inputs[figure][0].keys()
        if ids != cohorts.setdefault(cohort, ids):
            mismatched.add(cohort)
    for figure, spec in FIGURES.items():
        if spec[:2] in mismatched and figure in inputs:
            del inputs[figure]
            results[figure] = {
                "status": "blocked", "mean_tokens": None, "reason": "comparison arms cover different questions"
            }

    failed = bool(results)
    connection = http.client.HTTPSConnection("api.anthropic.com", timeout=60)
    try:
        for figure, (texts, reference_chars) in inputs.items():
            entry = {
                "status": "pending" if args.inspect else "counted",
                "questions": len(texts),
                "reference_mean_chars": reference_chars,
                "mean_rendered_chars": statistics.mean(map(len, texts.values())),
                "mean_tokens": None,
            }
            results[figure] = entry
            if args.inspect:
                continue
            try:
                # An empty context has zero content tokens and cannot be sent as an empty message.
                entry["mean_tokens"] = statistics.mean(count_tokens(connection, key, text) if text else 0 for text in texts.values())
            except (OSError, http.client.HTTPException, KeyError, ValueError) as error:
                entry.update(status="error", reason=type(error).__name__)
                print(f"Token counting stopped at {figure}. See the JSON output.", file=sys.stderr)
                failed = True
                break
    finally:
        connection.close()
    for figure in FIGURES.keys() - results.keys():
        results[figure] = {"status": "pending", "mean_tokens": None}
    output = {"model": MODEL, "anthropic_version": API_VERSION, "figures": results}
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
