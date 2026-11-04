"""ChromaDB setup: build the OSL collection and retrieve chunks."""
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

DB_PATH = Path(__file__).resolve().parents[1] / ".chroma"
# CoreML crashes on this model on some Macs; CPU is slower but reliable.
EMBEDDING_FUNCTION = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])


# Cosine distance (lower = closer) above which a chunk counts as unrelated.
# Handbook questions scored 0.35-0.65 in testing; off-topic ones scored 0.85+.
MAX_DISTANCE = 0.75


def build_collection(chunks: list[str]) -> chromadb.api.models.Collection.Collection:
    client = chromadb.PersistentClient(
        path=str(DB_PATH),
        settings=chromadb.Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        "osl_knowledge_base",
        embedding_function=EMBEDDING_FUNCTION,
    )
    if chunks:
        collection.upsert(
            ids=[f"osl-{i}" for i in range(len(chunks))],
            documents=chunks,
        )
    return collection


def retrieve(collection: chromadb.api.models.Collection.Collection, query: str, n_results: int = 3) -> list[str]:
    """Return the nearest chunks, dropping any farther than MAX_DISTANCE (empty list = no good match)."""
    results = collection.query(query_texts=[query], n_results=n_results)
    return [
        doc for doc, distance in zip(results["documents"][0], results["distances"][0])
        if distance <= MAX_DISTANCE
    ]
