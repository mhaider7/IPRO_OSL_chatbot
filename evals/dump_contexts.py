"""
dump_contexts.py — rebuild the handbook text behind each answer, for RAGAS.

Hawk's API returns the pages it cited, not the text. This reads the local handbook index
(read-only; built from the handbook only if this machine has none yet) and fetches the chunks for those pages,
in cited order.
Only the answerable handbook categories are kept (live 312 and refusal answers have no handbook context).

Usage (project venv):
    python evals/dump_contexts.py evals/results/<run_id>
Writes <run_id>/contexts.jsonl
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ots-chatbot-backend"))

import chromadb  # noqa: E402
from retrieval import chroma_setup as cs  # noqa: E402

HANDBOOK_CATEGORIES = ("answerable_fact", "answerable_procedure", "answerable_numbers")


def main(run_dir: Path):
    client = chromadb.PersistentClient(path=str(cs.DB_PATH), settings=chromadb.Settings(anonymized_telemetry=False))
    try:
        col = client.get_collection(cs.COLLECTION_NAME, embedding_function=cs.EMBEDDING_FUNCTION)
    except Exception:   # never started Hawk on this machine (e.g. grading someone else's answers): build the index
        from config.loader import load_config, load_docs
        col = cs.build_collection(load_docs(load_config().docs_path))

    def chunks_for(cite: str) -> list[tuple[str, int, int]]:
        m = re.search(r"pp?\. (\d+)(?:-(\d+))?", cite)
        if not m:
            return []
        start, end = int(m.group(1)), int(m.group(2) or m.group(1))
        got = col.get(where={"$and": [{"page_start": start}, {"page_end": end}]})
        return [(text, start, end) for text in got["documents"]]

    rows = [json.loads(l) for l in (run_dir / "raw.jsonl").open(encoding="utf-8") if l.strip()]
    keep = [r for r in rows if r["category"] in HANDBOOK_CATEGORIES and not r.get("error")]
    with (run_dir / "contexts.jsonl").open("w", encoding="utf-8") as f:
        for r in keep:
            found = [c for cite in r.get("sources", []) for c in chunks_for(cite)]
            f.write(json.dumps({
                "id": r["id"], "category": r["category"],
                "user_input": r["question"], "response": r["answer"],
                "reference": r.get("gold_answer") or "; ".join(r.get("key_facts", [])),
                "retrieved_contexts": [t for t, _, _ in found],
                "context_pages": [[s, e] for _, s, e in found],
                "gold_pages": sorted({p for g in r.get("gold_sources", []) for p in g.get("pages", [])}),
            }) + "\n")
    print(f"{len(keep)} handbook answers -> {run_dir / 'contexts.jsonl'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python evals/dump_contexts.py evals/results/<run_id>")
    main(Path(sys.argv[1]))
