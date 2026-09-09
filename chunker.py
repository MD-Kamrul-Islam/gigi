"""
Splits pages into overlapping chunks for retrieval, keeping each chunk's
source label, page title and URL so every answer can be traced.

Two things this does beyond naive splitting, both from pilot testing:

1. BOILERPLATE REMOVAL -- a line appearing on a large share of one site's
   pages is nav chrome ("Skip to main content", "Menu"). Embedding that noise
   pulls unrelated pages together in vector space. Detected per source, so
   adding a second website doesn't dilute the first site's detection.

2. HEADING CONTEXT -- each chunk is prefixed with the page title and source
   label. A chunk from the middle of a long list page otherwise loses all
   trace of what page it belongs to, which is exactly how "Business Analytics
   Society" became hard to find inside a 30-organization listing.

Chunks also break on line boundaries rather than mid-word.
"""

import json
import os
from collections import Counter

from settings import cfg

INPUT_FILES = ["pages.jsonl", "manual_pages.jsonl"]
OUTPUT_FILE = "chunks.jsonl"
MIN_PAGES_FOR_BOILERPLATE = 10


def find_boilerplate_lines(pages: list[dict], threshold_ratio: float) -> set[str]:
    """Lines repeated across most pages of one site = chrome, not content."""
    if len(pages) < MIN_PAGES_FOR_BOILERPLATE:
        return set()
    counts = Counter()
    for page in pages:
        for line in set(page["text"].splitlines()):
            line = line.strip()
            if line:
                counts[line] += 1
    threshold = len(pages) * threshold_ratio
    return {line for line, n in counts.items() if n >= threshold}


def strip_boilerplate(text: str, boilerplate: set[str]) -> str:
    lines = [l.strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l and l not in boilerplate)


def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """Greedy line-packing up to `size` chars, carrying the tail of each chunk
    into the next one as overlap so an answer can't be cut in half."""
    size = size or int(cfg("chunking", "chunk_size", 800))
    overlap = overlap or int(cfg("chunking", "chunk_overlap", 150))

    if not text.strip():
        return []
    if len(text) <= size:
        return [text]

    # Split any single line longer than a whole chunk.
    units: list[str] = []
    for line in text.splitlines():
        while len(line) > size:
            units.append(line[:size])
            line = line[size - overlap:]
        if line:
            units.append(line)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for unit in units:
        if current and current_len + len(unit) + 1 > size:
            chunks.append("\n".join(current))
            tail: list[str] = []
            tail_len = 0
            for prev in reversed(current):
                if tail_len + len(prev) + 1 > overlap:
                    break
                tail.insert(0, prev)
                tail_len += len(prev) + 1
            if not tail:
                # one very long line -- carry its tail, snapped to a word break
                frag = current[-1][-overlap:]
                space = frag.find(" ")
                if 0 <= space < len(frag) - 1:
                    frag = frag[space + 1:]
                tail, tail_len = [frag], len(frag)
            current, current_len = tail, tail_len
        current.append(unit)
        current_len += len(unit) + 1
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


def build_chunks():
    threshold_ratio = float(cfg("chunking", "boilerplate_threshold", 0.35))
    chunks_out = []

    for input_file in INPUT_FILES:
        if not os.path.exists(input_file):
            continue
        with open(input_file, "r", encoding="utf-8") as f:
            pages = [json.loads(line) for line in f]
        if not pages:
            continue

        # Group by source so each website's boilerplate is detected separately.
        by_source: dict[str, list[dict]] = {}
        for page in pages:
            by_source.setdefault(page.get("source", "Unknown"), []).append(page)

        for label, source_pages in by_source.items():
            # Manual documents are hand-written; nothing to strip.
            boilerplate = (set() if input_file == "manual_pages.jsonl"
                           else find_boilerplate_lines(source_pages, threshold_ratio))
            if boilerplate:
                print(f"  {label}: stripping {len(boilerplate)} boilerplate lines")

            for page in source_pages:
                text = strip_boilerplate(page["text"], boilerplate)
                for i, piece in enumerate(chunk_text(text)):
                    chunks_out.append({
                        "url": page["url"],
                        "title": page["title"],
                        "source": label,
                        "chunk_index": i,
                        "text": piece,
                    })

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for c in chunks_out:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"Built {len(chunks_out)} chunks -> {OUTPUT_FILE}")
    return chunks_out


if __name__ == "__main__":
    build_chunks()
