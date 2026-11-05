"""ChromaDB setup: build the OSL collection and retrieve chunks."""
import re
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from config.loader import Chunk

COLLECTION_NAME = "osl_knowledge_base"
DB_PATH = Path(__file__).resolve().parents[1] / ".chroma"
# CoreML crashes on this model on some Macs; CPU is slower but reliable.
EMBEDDING_FUNCTION = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])


# Cosine distance (lower = closer) above which a chunk counts as unrelated.
# Handbook questions scored 0.35-0.65 in testing; off-topic ones scored 0.85+.
MAX_DISTANCE = 0.75


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_collection(chunks: list[Chunk]) -> chromadb.api.models.Collection.Collection:
    """Rebuild the collection from scratch so chunks from edited docs never go stale."""
    client = chromadb.PersistentClient(
        path=str(DB_PATH),
        settings=chromadb.Settings(anonymized_telemetry=False),
    )
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:  # collection does not exist yet
        pass
    collection = client.create_collection(
        COLLECTION_NAME,
        embedding_function=EMBEDDING_FUNCTION,
    )
    if chunks:
        seen: dict[str, int] = {}
        ids, metadatas = [], []
        for chunk in chunks:
            base = f"{_slug(chunk.source)}::{_slug(chunk.path)}"
            n = seen[base] = seen.get(base, -1) + 1
            ids.append(f"{base}::{n}")
            meta = {"source": chunk.source, "path": chunk.path, "section": chunk.section}
            if chunk.page_start is not None:  # Chroma rejects None values
                meta["page_start"] = chunk.page_start
                meta["page_end"] = chunk.page_end
            metadatas.append(meta)
        collection.upsert(ids=ids, documents=[c.text for c in chunks], metadatas=metadatas)
    return collection


def retrieve(collection: chromadb.api.models.Collection.Collection, query: str, n_results: int = 3) -> list[dict]:
    """Return the nearest chunks as {text, source, path, page_start, page_end}, dropping any
    farther than MAX_DISTANCE (empty list = no good match)."""
    results = collection.query(query_texts=[query], n_results=n_results)
    return [
        {"text": doc, **(meta or {})}
        for doc, meta, distance in zip(results["documents"][0], results["metadatas"][0], results["distances"][0])
        if distance <= MAX_DISTANCE
    ]
