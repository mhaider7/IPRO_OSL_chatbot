"""Load the OSL config and read its docs for ingestion into ChromaDB."""
from dataclasses import dataclass
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent


@dataclass
class Config:
    name: str
    short_name: str
    email: str
    docs_path: Path
    model: str
    system_prompt: str


def load_config() -> Config:
    path = BASE / "config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Config(
        name=raw["name"],
        short_name=raw["short_name"],
        email=raw["email"],
        docs_path=(BASE.parent / raw["docs_path"]).resolve(),
        model=raw["model"],
        system_prompt=raw["system_prompt"].strip(),
    )


def load_docs(docs_path: Path) -> list[str]:
    """Return all text chunks from .pdf and .txt files in docs_path."""
    chunks = []
    for file in sorted(docs_path.iterdir()):
        if file.suffix.lower() == ".pdf":
            chunks.extend(_pdf_chunks(file))
        elif file.suffix.lower() == ".txt":
            chunks.extend(_text_chunks(file.read_text(encoding="utf-8")))
    return chunks


def _pdf_chunks(path: Path) -> list[str]:
    import pypdf
    reader = pypdf.PdfReader(str(path))
    text = "\n\n".join(
        page.extract_text() or "" for page in reader.pages
    )
    return _text_chunks(text)


def _text_chunks(text: str) -> list[str]:
    """Split on double newlines (paragraphs); skip blank chunks."""
    return [c.strip() for c in text.split("\n\n") if len(c.strip()) > 40]
