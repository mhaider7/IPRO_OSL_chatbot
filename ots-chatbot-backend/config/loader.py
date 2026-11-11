"""Load the OSL config and read its docs for ingestion into ChromaDB.

Docs are split into section-sized chunks:
- PDFs use the document's built-in outline (bookmarks) as the section map.
- .md/.txt files use Markdown headings (#, ##, ###).
Documents with neither fall back to paragraph splitting.
"""
import logging
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
logger = logging.getLogger("hawk")

MIN_CHARS = 150    # sections shorter than this are merged into a neighbour
MAX_CHARS = 2000   # sections longer than this are split on paragraph breaks
SEP = " › "   # breadcrumb separator
DOC_SUFFIXES = {".pdf", ".txt", ".md"}


@dataclass
class Config:
    name: str
    short_name: str
    email: str
    docs_path: Path
    model: str
    system_prompt: str
    web_tools: dict = field(default_factory=dict)   # optional overrides for web_tools.config


@dataclass
class Chunk:
    text: str                 # body, prefixed with the breadcrumb path
    source: str               # file name
    path: str                 # "Finances > Event Charges > Chartwells Charges"
    section: str              # leaf heading
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class _Section:
    titles: tuple[str, ...]   # heading path, outermost first
    body: str
    page_start: int | None = None
    page_end: int | None = None

    @property
    def level(self) -> int:
        return len(self.titles) - 1


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
        web_tools=raw.get("web_tools") or {},
    )


def load_docs(docs_path: Path) -> list[Chunk]:
    """Return section chunks from every .pdf, .md and .txt file in docs_path."""
    chunks: list[Chunk] = []
    for file in sorted(docs_path.iterdir()):
        suffix = file.suffix.lower()
        if suffix not in DOC_SUFFIXES:
            continue
        if suffix == ".pdf":
            chunks.extend(_pdf_chunks(file))
        else:
            chunks.extend(_md_chunks(file.name, file.read_text(encoding="utf-8")))
    return chunks


# --- PDF -------------------------------------------------------------------

def _pdf_chunks(path: Path) -> list[Chunk]:
    import pypdf
    reader = pypdf.PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    entries = _flatten_outline(reader, reader.outline)
    if not entries:
        logger.warning("%s has no outline; falling back to paragraph chunks", path.name)
        return _paragraph_chunks(path.name, "\n\n".join(pages))
    return _finalize(path.name, _pdf_sections(path.name, pages, entries))


def _flatten_outline(reader, outline, parents: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], int]]:
    """Flatten nested outline items into [(title path, 0-based page)] in document order."""
    entries = []
    for item in outline:
        if isinstance(item, list):
            # A list holds the children of the item just before it.
            entries.extend(_flatten_outline(reader, item, entries[-1][0] if entries else parents))
        else:
            entries.append(((*parents, item.title.strip()), reader.get_destination_page_number(item)))
    return entries


def _char_pattern(c: str) -> str:
    if c in "'‘’":
        return "['‘’]"
    if c in '"“”':
        return '["“”]'
    if c in "-–—":
        return "[-–—]"
    return re.escape(c)


def _heading_regex(title: str, anchored: bool) -> re.Pattern:
    body = r"\s+".join("".join(_char_pattern(c) for c in word) for word in title.split())
    if anchored:  # heading alone on its line
        return re.compile(rf"^[ \t]*{body}[ \t]*$", re.IGNORECASE | re.MULTILINE)
    return re.compile(body, re.IGNORECASE)


def _pdf_sections(source: str, pages: list[str], entries) -> list[_Section]:
    text = "\n\n".join(pages)
    page_starts, pos = [], 0
    for page in pages:
        page_starts.append(pos)
        pos += len(page) + 2

    def page_of(offset: int) -> int:
        return bisect_right(page_starts, offset) - 1  # 0-based

    # Locate each heading in the text; (heading_start, body_start).
    spans, cursor = [], 0
    for titles, page in entries:
        start = max(cursor, page_starts[page])
        match = None
        for anchored in (True, False):
            match = _heading_regex(titles[-1], anchored).search(text, start)
            if match:
                break
        if match:
            spans.append((match.start(), match.end()))
            cursor = match.end()
        else:
            logger.warning("%s: heading %r not found on page %d", source, SEP.join(titles), page + 1)
            spans.append((start, start))
            cursor = start

    sections = []
    for i, (titles, _) in enumerate(entries):
        body_start = spans[i][1]
        body_end = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        body_end = max(body_end, body_start)
        sections.append(_Section(
            titles=titles,
            body=text[body_start:body_end],
            page_start=page_of(spans[i][0]) + 1,
            page_end=page_of(max(body_end - 1, body_start)) + 1,
        ))
    return sections


# --- Markdown / text -------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def _md_chunks(source: str, text: str) -> list[Chunk]:
    sections: list[_Section] = []
    stack: list[str] = []
    titles: tuple[str, ...] = (source,)
    lines: list[str] = []

    def flush():
        body = "\n".join(lines)
        if body.strip() or titles != (source,):  # skip an empty preamble
            sections.append(_Section(titles=titles, body=body))

    for line in text.splitlines():
        m = _MD_HEADING.match(line)
        if not m:
            lines.append(line)
            continue
        flush()
        lines = []
        level = len(m.group(1))
        stack = stack[:level - 1]
        stack += [""] * (level - 1 - len(stack))  # skipped heading levels
        stack.append(m.group(2))
        titles = tuple(t for t in stack if t)
    flush()

    if not any(s.titles != (source,) for s in sections):  # no headings at all
        return _paragraph_chunks(source, text)
    return _finalize(source, sections)


# --- Shared ----------------------------------------------------------------

def _clean(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _paragraph_chunks(source: str, text: str) -> list[Chunk]:
    """Fallback: one chunk per paragraph (blank-line separated), skipping tiny ones."""
    return [
        Chunk(text=c.strip(), source=source, path=source, section=source)
        for c in text.split("\n\n") if len(c.strip()) > 40
    ]


def _split_text(text: str, limit: int) -> list[str]:
    """Pack paragraphs (then lines, for oversized paragraphs) into pieces of at most `limit` chars."""
    units = []
    for para in text.split("\n\n"):
        units.extend([para] if len(para) <= limit else para.split("\n"))
    pieces, current = [], ""
    for unit in units:
        joined = f"{current}\n\n{unit}" if current else unit
        if current and len(joined) > limit:
            pieces.append(current)
            current = unit
        else:
            current = joined
    if current:
        pieces.append(current)
    return pieces


def _finalize(source: str, sections: list[_Section]) -> list[Chunk]:
    """Merge tiny sections into neighbours, split huge ones, and attach breadcrumbs."""
    merged: list[_Section] = []
    carry = ""  # text of a short parent section waiting to join the next section
    for i, sec in enumerate(sections):
        body = _clean(sec.body)
        title_line = sec.titles[-1]
        if carry:
            body = f"{carry}\n\n{body}".strip()
            carry = ""
        if len(body) >= MIN_CHARS:
            merged.append(_Section(sec.titles, body, sec.page_start, sec.page_end))
            continue
        nxt = sections[i + 1] if i + 1 < len(sections) else None
        prev = merged[-1] if merged else None
        has_child = nxt is not None and nxt.level > sec.level
        same_top = prev is not None and prev.titles[0] == sec.titles[0]
        if prev is not None and same_top and sec.level > 0 and not has_child:
            prev.body = f"{prev.body}\n\n{title_line}\n{body}".strip()
            prev.page_end = sec.page_end
        elif nxt is not None:
            carry = f"{title_line}\n{body}".strip() if body else title_line
        elif prev is not None:
            prev.body = f"{prev.body}\n\n{title_line}\n{body}".strip()
            prev.page_end = sec.page_end
        elif body:
            merged.append(_Section(sec.titles, body, sec.page_start, sec.page_end))

    chunks = []
    for sec in merged:
        path = SEP.join(sec.titles)
        for piece in _split_text(sec.body, MAX_CHARS):
            chunks.append(Chunk(
                text=f"{path}\n{piece}",
                source=source,
                path=path,
                section=sec.titles[-1],
                page_start=sec.page_start,
                page_end=sec.page_end,
            ))
    return chunks
