import logging

import pypdf
import pytest

from config import loader
from config.loader import MAX_CHARS, SEP, load_config, load_docs


@pytest.fixture(scope="module")
def handbook_chunks():
    return [c for c in load_docs(load_config().docs_path) if c.source.endswith(".pdf")]


def test_every_outline_entry_is_matched(caplog):
    with caplog.at_level(logging.WARNING, logger="hawk"):
        load_docs(load_config().docs_path)
    assert "not found" not in caplog.text


def test_pdf_chunks_have_metadata(handbook_chunks):
    assert handbook_chunks
    for c in handbook_chunks:
        assert c.text.startswith(c.path)
        assert c.section and c.path.endswith(c.section)
        assert 5 <= c.page_start <= c.page_end <= 45


def test_pdf_chunks_in_page_order(handbook_chunks):
    starts = [c.page_start for c in handbook_chunks]
    assert starts == sorted(starts)


def test_pdf_chunks_respect_max_size(handbook_chunks):
    for c in handbook_chunks:
        assert len(c.text) <= MAX_CHARS + len(c.path) + 1


def test_every_top_level_outline_section_is_covered(handbook_chunks):
    reader = pypdf.PdfReader(str(load_config().docs_path / "student_org_handbook.pdf"))
    top = {item.title.strip() for item in reader.outline if not isinstance(item, list)}
    covered = {c.path.split(SEP)[0] for c in handbook_chunks}
    assert top <= covered


def test_front_matter_table_of_contents_skipped(handbook_chunks):
    assert not any("......" in c.text for c in handbook_chunks)


def test_markdown_nested_headings_and_preamble():
    body = "x" * 400
    text = f"Intro paragraph. {body}\n\n# Funding\n{body}\n## Travel\n{body}\n### Flights\n{body}\n# Events\n{body}\n"
    chunks = loader._md_chunks("faq.md", text)
    assert [c.path for c in chunks] == [
        "faq.md",
        "Funding",
        f"Funding{SEP}Travel",
        f"Funding{SEP}Travel{SEP}Flights",
        "Events",
    ]
    assert all(c.page_start is None for c in chunks)


def test_markdown_short_child_merges_into_previous_sibling():
    long, short = "y" * 400, "short note"
    text = f"# Funding\n{long}\n## Travel\n{long}\n## Misc\n{short}\n"
    chunks = loader._md_chunks("faq.md", text)
    assert [c.path for c in chunks] == ["Funding", f"Funding{SEP}Travel"]
    assert short in chunks[-1].text


def test_markdown_long_section_is_split():
    para = "z" * 900
    text = f"# Big\n{para}\n\n{para}\n\n{para}\n"
    chunks = loader._md_chunks("faq.md", text)
    assert len(chunks) > 1
    assert all(c.path == "Big" and len(c.text) <= MAX_CHARS + 5 for c in chunks)


def test_text_without_headings_falls_back_to_paragraphs():
    text = "First paragraph that is long enough to keep around.\n\nTiny\n\nSecond paragraph that is also long enough to keep."
    chunks = loader._md_chunks("notes.txt", text)
    assert len(chunks) == 2
    assert chunks[0].path == "notes.txt"


# ---- search picker settings: config.yaml + per-laptop environment overrides (R, 2026-11-17) ------------------
from config.loader import search_picker_settings  # noqa: E402


def test_search_picker_settings_come_from_the_config_by_default():
    assert search_picker_settings({"enabled": True, "timeout_s": 5}, env={}) == {"enabled": True, "timeout_s": 5}


def test_a_cpu_laptop_can_switch_the_picker_off_or_wait_longer_without_editing_the_config():
    base = {"enabled": True, "timeout_s": 5}
    assert search_picker_settings(base, env={"HAWK_SEARCH_PICKER": "off"})["enabled"] is False
    assert search_picker_settings({"enabled": False}, env={"HAWK_SEARCH_PICKER": "on"})["enabled"] is True
    assert search_picker_settings(base, env={"HAWK_PICKER_TIMEOUT_S": "60"})["timeout_s"] == 60.0
    for bad in ("", "soon", "-3", "0"):
        assert search_picker_settings(base, env={"HAWK_PICKER_TIMEOUT_S": bad})["timeout_s"] == 5, bad
    assert base == {"enabled": True, "timeout_s": 5}   # the config's own dict is not changed
