"""
Loads your own .md / .txt files from manual_docs/ into the same shape as
scraped pages, so the chunker and vector store treat them identically.

Manual docs are how you fill gaps the website doesn't cover: the full RSO
list (including Graduate Business Birds), class-registration pointers, GBB
event info, career advice. They're also where admin-written corrections will
land in Phase 3c.

Each file's name becomes its title, so Gigi can still say where an answer
came from even though there's no live URL.
"""

import json
import os

INPUT_DIR = "manual_docs"
OUTPUT_FILE = "manual_pages.jsonl"
SOURCE_LABEL = "GBB / added by admin"


def load_manual_docs():
    if not os.path.isdir(INPUT_DIR):
        os.makedirs(INPUT_DIR, exist_ok=True)
        print(f"Created empty {INPUT_DIR}/ -- drop .md or .txt files there and re-run.")
        return []

    pages = []
    for filename in sorted(os.listdir(INPUT_DIR)):
        if not filename.lower().endswith((".md", ".txt")):
            continue
        with open(os.path.join(INPUT_DIR, filename), "r", encoding="utf-8") as f:
            text = f.read().strip()
        if len(text) < 10:
            continue
        pages.append({
            "url": f"internal://{filename}",
            "title": os.path.splitext(filename)[0].replace("-", " ").replace("_", " ").title(),
            "text": text,
            "source": SOURCE_LABEL,
        })
        print(f"loaded manual doc: {filename}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"Loaded {len(pages)} manual document(s) -> {OUTPUT_FILE}")
    return pages


if __name__ == "__main__":
    load_manual_docs()
