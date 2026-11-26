"""
ragas_eval.py — official RAGAS scores for Hawk's handbook answers.

Metrics (0-1, higher is better):
  context_precision  did search rank the chunks that hold the answer first?   (LLMContextPrecisionWithReference)
  context_recall     does the retrieved text contain the reference answer?    (LLMContextRecall)
  faithfulness       is every claim in Hawk's reply supported by that text?   (Faithfulness)
  answer_relevancy   does the reply address the question?                     (ResponseRelevancy)

Judge: a local Ollama model (default gemma4:e4b); embeddings: all-minilm (`ollama pull all-minilm`).
Runs in its own venv so the chatbot's dependencies stay untouched:
    python -m venv .venv-eval && .venv-eval/Scripts/pip install -r evals/requirements-eval.txt

Usage:
    python evals/ragas_eval.py evals/results/<run_id> [--judge gemma4:e4b] [--limit N]
Reads <run_id>/contexts.jsonl (from dump_contexts.py); saves each answer's scores to <run_id>/ragas_items.jsonl
as it goes (run again after a crash and it continues), then writes the summary to <run_id>/ragas.json
"""
import argparse
import json
import math
import os
import time
from pathlib import Path

from langchain_ollama import ChatOllama, OllamaEmbeddings
from ragas import EvaluationDataset, RunConfig, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics._answer_relevance import ResponseRelevancy
from ragas.metrics._context_precision import LLMContextPrecisionWithReference
from ragas.metrics._context_recall import LLMContextRecall
from ragas.metrics._faithfulness import Faithfulness

METRICS = {
    "context_precision": LLMContextPrecisionWithReference,
    "context_recall": LLMContextRecall,
    "faithfulness": Faithfulness,
    "answer_relevancy": ResponseRelevancy,
}


def clean(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)


def main(run_dir: Path, judge: str, limit: int | None, pause: float = 0.0):
    rows = [json.loads(l) for l in (run_dir / "contexts.jsonl").open(encoding="utf-8") if l.strip()]
    # An answer with no cited pages ("I couldn't find it") has no context to grade.
    scored = [r for r in rows if r["retrieved_contexts"] and r["response"]][:limit]
    cols = {name: cls().name for name, cls in METRICS.items()}

    # One answer at a time, each saved as it finishes, so a crash loses at most one answer's scores.
    part = run_dir / "ragas_items.jsonl"
    done = {json.loads(l)["id"]: json.loads(l) for l in part.open(encoding="utf-8") if l.strip()} if part.exists() else {}
    with part.open("a", encoding="utf-8") as f:
        for i, r in enumerate(scored, 1):
            if r["id"] in done:
                continue
            if pause:
                time.sleep(pause)
            # Fresh clients per call: evaluate() runs its own event loop, and a client reused from a closed loop
            # fails with "Event loop is closed" (which silently blanked context_precision).
            llm = LangchainLLMWrapper(ChatOllama(model=judge, temperature=0, reasoning=False, num_ctx=8192))
            emb = LangchainEmbeddingsWrapper(OllamaEmbeddings(model="all-minilm"))
            sample = {k: r[k] for k in ("user_input", "response", "retrieved_contexts", "reference")}
            df = evaluate(EvaluationDataset.from_list([sample]), metrics=[cls() for cls in METRICS.values()],
                          llm=llm, embeddings=emb, run_config=RunConfig(timeout=600, max_workers=1, max_retries=2),
                          raise_exceptions=False, show_progress=False).to_pandas()
            item = {"id": r["id"], "category": r["category"], **{k: clean(df.iloc[0][c]) for k, c in cols.items()}}
            done[r["id"]] = item
            f.write(json.dumps(item) + "\n")
            f.flush()
            os.fsync(f.fileno())
            print(f"[{i}/{len(scored)}] {r['id']} " + " ".join(f"{k}={item[k]}" for k in METRICS), flush=True)
    items = [done[r["id"]] for r in scored]
    summary = {}
    for k in METRICS:
        vals = [it[k] for it in items if it[k] is not None]
        summary[k] = {"mean": round(sum(vals) / len(vals), 3) if vals else None,
                      "n_scored": len(vals), "n_failed": len(items) - len(vals)}
    out = {"judge": judge, "embeddings": "all-minilm", "ragas_version": __import__("ragas").__version__,
           "n_handbook_answers": len(rows), "n_without_context": len(rows) - len([r for r in rows if r["retrieved_contexts"]]),
           "summary": summary, "items": items}
    (run_dir / "ragas.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--judge", default="gemma4:e4b")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pause", type=float, default=0.0, help="seconds to rest between answers")
    a = ap.parse_args()
    main(a.run_dir, a.judge, a.limit, a.pause)
