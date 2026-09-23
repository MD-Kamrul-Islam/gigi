"""
Corrections: answers you have personally verified, which override whatever
the scraped site said.

This is how Gigi improves with use. A student reports a wrong answer, you write
the right one, and from the next rebuild onward that correction is retrieved
(and boosted) ahead of the page that caused the problem.

Storage is a structured JSON file rather than a markdown document, so entries
can be edited and deleted individually instead of hand-editing prose. At
ingest time each correction is turned into a small "page" that flows through
the normal chunk-and-embed pipeline, labelled as a verified answer.

Deliberately NOT automatic: nothing a student submits ever becomes a
correction on its own. Unsupervised learning from anonymous feedback is how
chatbots end up confidently wrong. You are the gate.
"""

import json
import os
from datetime import datetime, timezone

CORRECTIONS_FILE = "corrections.json"
PAGES_FILE = "corrections_pages.jsonl"

# Shown to students as the source, and used by the retrieval boost to
# recognise admin-verified content.
CORRECTION_LABEL = "Verified by GBB"


def load_corrections() -> list[dict]:
    if not os.path.exists(CORRECTIONS_FILE):
        return []
    try:
        with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[corrections] could not read {CORRECTIONS_FILE}: {e}")
        return []


def _save_all(corrections: list[dict]):
    with open(CORRECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(corrections, f, indent=2, ensure_ascii=False)


def _next_id(corrections: list[dict]) -> int:
    return max((c.get("id", 0) for c in corrections), default=0) + 1


def add_correction(question: str, answer: str, link: str = "",
                   note: str = "") -> dict:
    """Record a verified answer. `question` is the student's wording -- keep it
    close to how they actually asked, since that is what retrieval matches
    against."""
    corrections = load_corrections()
    entry = {
        "id": _next_id(corrections),
        "question": question.strip(),
        "answer": answer.strip(),
        "link": link.strip(),
        "note": note.strip(),
        "created": datetime.now(timezone.utc).isoformat(),
    }
    corrections.append(entry)
    _save_all(corrections)
    return entry


def update_correction(correction_id: int, **fields) -> dict | None:
    corrections = load_corrections()
    for entry in corrections:
        if entry.get("id") == correction_id:
            for key in ("question", "answer", "link", "note"):
                if key in fields and fields[key] is not None:
                    entry[key] = str(fields[key]).strip()
            entry["updated"] = datetime.now(timezone.utc).isoformat()
            _save_all(corrections)
            return entry
    return None


def delete_correction(correction_id: int) -> bool:
    corrections = load_corrections()
    remaining = [c for c in corrections if c.get("id") != correction_id]
    if len(remaining) == len(corrections):
        return False
    _save_all(remaining)
    return True


def build_correction_pages() -> list[dict]:
    """Turn corrections into pages for the ingest pipeline.

    Each correction becomes its own page so it stays a tight, focused chunk --
    one question and its verified answer. Bundling them into one document would
    dilute each entry's embedding with unrelated topics, which is exactly the
    problem we hit with the 30-organization RSO listing.
    """
    corrections = load_corrections()
    pages = []
    for entry in corrections:
        text = f"Question: {entry['question']}\nVerified answer: {entry['answer']}"
        if entry.get("link"):
            text += f"\nSource: {entry['link']}"
        if entry.get("note"):
            text += f"\nNote: {entry['note']}"
        pages.append({
            "url": entry.get("link") or f"internal://correction-{entry['id']}",
            "title": entry["question"][:90],
            "text": text,
            "source": CORRECTION_LABEL,
        })

    with open(PAGES_FILE, "w", encoding="utf-8") as f:
        for page in pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")

    print(f"Prepared {len(pages)} correction(s) -> {PAGES_FILE}")
    return pages


if __name__ == "__main__":
    build_correction_pages()
