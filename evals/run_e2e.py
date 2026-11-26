"""
run_e2e.py — sends every test item through /api/chat and saves the results.

Usage:
    python evals/run_e2e.py                        # runs all items
    python evals/run_e2e.py --split test           # test split only
    python evals/run_e2e.py --split dev            # dev split only
    python evals/run_e2e.py --category adversarial # one category
    python evals/run_e2e.py --resume evals/results/<run_id>   # continue a run that stopped
    python evals/run_e2e.py --pause 3              # rest 3 s between questions (keeps a laptop GPU cooler)

Output: evals/results/<date>_<commit>/raw.jsonl
The "behavior" field is a rough keyword guess; judge.py writes the real labels used for scoring.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_URL = os.getenv("HAWK_URL", "http://localhost:8001")
TESTSET  = Path(__file__).parent / "testset_v1.jsonl"
RESULTS  = Path(__file__).parent / "results"


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:
        return "unknown"


def stream_chat(question: str) -> dict:
    """POST /api/chat and return the response."""
    import urllib.request

    payload = json.dumps({
        "messages": [{"role": "user", "text": question}]
    }).encode()

    req = urllib.request.Request(
        f"{BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    t_start = time.perf_counter()

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:   # CPU-only laptops can take minutes
            body = json.loads(resp.read().decode("utf-8"))
        return {
            "answer":              body.get("reply", ""),
            "sources":             body.get("sources", []),
            "stages":              [],
            "escalate":            body.get("escalate", False),
            "latency_total":       time.perf_counter() - t_start,
            "latency_first_token": None,
            "error":               None,
        }
    except Exception as e:
        return {"error": str(e), "answer": "", "sources": [], "stages": [],
                "escalate": False,
                "latency_total": time.perf_counter() - t_start,
                "latency_first_token": None}


def infer_behavior(result: dict) -> str:
    """Guess what Hawk did from the response."""
    if result.get("error"):
        return "error"
    if result.get("escalate"):
        return "escalated"
    answer = result.get("answer", "").lower()
    # Refusal patterns from the system prompt
    refuse_phrases = [
        "only help with osl",
        "only cover osl",
        "i only",
        "that's outside",
        "outside of osl",
        "not something i can help",
        "can't help with",
        "988",   # crisis line — always a refuse/escalate
        "911",
    ]
    if any(p in answer for p in refuse_phrases):
        return "refused"
    if not answer.strip():
        return "empty"
    return "answered"


def run(split=None, category=None, resume=None, pause=0.0):
    if not TESTSET.exists():
        print(f"Test set not found: {TESTSET}")
        sys.exit(1)

    items = []
    with open(TESTSET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            items.append(json.loads(line))

    if split:
        items = [i for i in items if i.get("split") == split]
    if category:
        items = [i for i in items if i.get("category") == category]

    if not items:
        print("No items matched the filters.")
        sys.exit(1)

    out_dir = Path(resume) if resume else RESULTS / f"{datetime.now().strftime('%Y%m%d_%H%M')}_{git_commit()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "raw.jsonl"
    done = set()
    if resume and out_file.exists():
        done = {json.loads(l)["id"] for l in out_file.open(encoding="utf-8") if l.strip()}
    todo = [it for it in items if it["id"] not in done]

    print(f"Running {len(todo)} items ({len(done)} already done) → {out_file}")
    print(f"Server: {BASE_URL}\n")

    latencies = []

    with open(out_file, "a" if done else "w", encoding="utf-8") as out:
        for i, item in enumerate(todo, 1):
            if pause and i > 1:
                time.sleep(pause)
            print(f"[{i}/{len(todo)}] {item['id']}  {item['question'][:60]}...")
            result = stream_chat(item["question"])
            behavior = infer_behavior(result)

            row = {
                "id":        item["id"],
                "category":  item.get("category"),
                "split":     item.get("split"),
                "question":  item["question"],
                "expected_behavior": item.get("expected_behavior"),
                "gold_answer":       item.get("gold_answer", ""),
                "key_facts":         item.get("key_facts", []),
                "gold_sources":      item.get("gold_sources", []),
                **result,
                "behavior": behavior,
            }
            row["asked_at"] = datetime.now().isoformat(timespec="seconds")
            out.write(json.dumps(row) + "\n")
            out.flush()
            os.fsync(out.fileno())   # on disk now: a power cut keeps every finished answer for --resume

            if result.get("latency_total"):
                latencies.append(result["latency_total"])

            guess = {"answered": "answer", "escalated": "escalate", "refused": "refuse"}.get(behavior, behavior)
            status = "✓" if guess == item.get("expected_behavior") else "✗"
            err = f"  ERROR: {result['error']}" if result.get("error") else ""
            print(f"  {status} behavior={behavior}  latency={result.get('latency_total', 0):.1f}s{err}")

    # Quick summary
    print(f"\nDone. Results saved to {out_file}")
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        print(f"Latency  p50={p50:.1f}s  p95={p95:.1f}s")

    print("Next: python evals/judge.py, score.py, dump_contexts.py, ragas_eval.py, report.py on", out_dir)
    return out_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split",    help="dev or test")
    parser.add_argument("--category", help="filter by category")
    parser.add_argument("--resume",   help="results folder of a run to continue")
    parser.add_argument("--pause",    type=float, default=0.0, help="seconds to rest between questions")
    args = parser.parse_args()
    run(split=args.split, category=args.category, resume=args.resume, pause=args.pause)
