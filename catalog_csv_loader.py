"""
Loads structured CSV data -- the ISU course catalog, or any other row-based
dataset -- into Gigi's knowledge base.

WHY THIS EXISTS INSTEAD OF CONVERTING THE CSV TO MARKDOWN

A CSV converted to one big markdown file gets chunked by character count, so
each chunk ends up holding several unrelated courses. A chunk's embedding is an
average of everything inside it, so no individual course gets a clean signal --
the same problem that made one organisation hard to find inside a 30-item list.
Prerequisites also get severed at chunk boundaries.

This loader gives each row its own page, so one course is one chunk: precise to
retrieve, complete when read, and correctly cited.

USAGE
  1. Put your .csv file(s) in the catalog_csv/ folder.
  2. Run `python ingest.py` (or press Rebuild in the admin dashboard).

Column names are auto-detected from common spellings. If your file uses
something unusual, add a mapping under `catalog:` in config.yaml -- see
COLUMN_GUESSES below for what's recognised automatically.
"""

import csv
import json
import os

from settings import cfg

INPUT_DIR = "catalog_csv"
OUTPUT_FILE = "catalog_pages.jsonl"
LABELS_FILE = "catalog_labels.json"
DEFAULT_LABEL = "ISU Catalog"

# Lowercased column names we recognise without configuration. Each field maps
# to a list of spellings seen in real catalog exports.
COLUMN_GUESSES = {
    "code": ["code", "course code", "course_code", "course", "course number",
             "course_number", "catalog number", "subject and number", "number"],
    "title": ["title", "course title", "course_title", "name", "course name"],
    "description": ["description", "course description", "desc", "catalog description",
                    "course_description", "summary"],
    "credits": ["credits", "credit hours", "credit_hours", "hours", "units",
                "credit", "sch"],
    "prerequisites": ["prerequisites", "prerequisite", "prereq", "prereqs",
                      "prerequisite(s)", "requirements", "prerequisites/corequisites"],
    "department": ["department", "dept", "subject", "program", "college",
                   "discipline"],
    "level": ["level", "career", "course level", "undergraduate/graduate"],
}


def load_labels() -> dict:
    """Per-file source labels, set when a CSV is uploaded in the dashboard.

    Each dataset gets cited by its own name, so a scholarships CSV isn't
    attributed to "ISU Catalog". Falls back to the config default for files
    uploaded before labels existed, or copied in by hand.
    """
    if not os.path.exists(LABELS_FILE):
        return {}
    try:
        with open(LABELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_label(filename: str, label: str):
    labels = load_labels()
    labels[filename] = label.strip() or DEFAULT_LABEL
    with open(LABELS_FILE, "w", encoding="utf-8") as f:
        json.dump(labels, f, indent=2, ensure_ascii=False)


def remove_label(filename: str):
    labels = load_labels()
    if labels.pop(filename, None) is not None:
        with open(LABELS_FILE, "w", encoding="utf-8") as f:
            json.dump(labels, f, indent=2, ensure_ascii=False)


def inspect_csv(path: str) -> dict:
    """Read a CSV's headers and count its rows without loading it all.

    Used by the dashboard to show, right after upload, which columns were
    recognised -- so a mismatch is caught before a rebuild rather than after.
    """
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return {"columns": [], "matched": {}, "rows": 0,
                        "error": "No header row found."}
            mapping = detect_columns(reader.fieldnames)
            rows = sum(1 for _ in reader)
            return {"columns": list(reader.fieldnames), "matched": mapping,
                    "rows": rows, "error": None}
    except Exception as e:
        return {"columns": [], "matched": {}, "rows": 0, "error": str(e)}


def _normalize(name: str) -> str:
    return (name or "").strip().lower().replace("-", " ").replace("_", " ")


def detect_columns(fieldnames: list[str]) -> dict:
    """Map our field names to whatever this CSV actually calls them.

    Explicit mappings in config.yaml win; anything unmapped is guessed. A field
    that can't be matched is simply left out rather than guessed wrongly.
    """
    configured = cfg("catalog", "columns", {}) or {}
    available = {_normalize(f): f for f in fieldnames}
    mapping = {}

    for field, guesses in COLUMN_GUESSES.items():
        # 1. explicit config mapping
        if field in configured:
            wanted = _normalize(configured[field])
            if wanted in available:
                mapping[field] = available[wanted]
                continue
        # 2. exact match against known spellings
        for guess in guesses:
            if guess in available:
                mapping[field] = available[guess]
                break
        else:
            # 3. partial match, e.g. "Course Title (long)" -> title
            for norm, original in available.items():
                if any(guess in norm for guess in guesses):
                    mapping[field] = original
                    break
    return mapping


def row_to_page(row: dict, mapping: dict, label: str, source_file: str) -> dict | None:
    """Turn one CSV row into a page. Returns None for rows too empty to be
    worth embedding (blank lines, section separators)."""
    def value(field: str) -> str:
        column = mapping.get(field)
        return (row.get(column) or "").strip() if column else ""

    code, title = value("code"), value("title")
    if not code and not title:
        return None

    heading = " ".join(p for p in [code, title] if p)

    # Written as sentences rather than a table row: embeddings match natural
    # language far better than pipe-delimited fields, and this text is also
    # what the model reads when answering.
    lines = [heading]
    if value("department"):
        lines.append(f"Department: {value('department')}")
    if value("credits"):
        lines.append(f"Credit hours: {value('credits')}")
    if value("level"):
        lines.append(f"Level: {value('level')}")
    if value("description"):
        lines.append(f"Description: {value('description')}")
    if value("prerequisites"):
        lines.append(f"Prerequisites: {value('prerequisites')}")

    # Keep any extra columns we didn't map -- catalogs carry useful oddities
    # (terms offered, attributes, fees) and dropping them silently loses data.
    mapped_columns = set(mapping.values())
    for column, cell in row.items():
        if column in mapped_columns or not cell:
            continue
        cell = str(cell).strip()
        if cell and len(cell) < 300:
            lines.append(f"{column.strip()}: {cell}")

    return {
        "url": f"internal://{source_file}#{code or title}",
        "title": heading,
        "text": "\n".join(lines),
        "source": label,
    }


def load_catalog_csvs():
    default_label = cfg("catalog", "label", DEFAULT_LABEL)
    labels = load_labels()

    if not os.path.isdir(INPUT_DIR):
        os.makedirs(INPUT_DIR, exist_ok=True)
        print(f"Created empty {INPUT_DIR}/ -- drop your catalog .csv files there.")
        open(OUTPUT_FILE, "w", encoding="utf-8").close()
        return []

    pages = []
    csv_files = [f for f in sorted(os.listdir(INPUT_DIR)) if f.lower().endswith(".csv")]

    for filename in csv_files:
        path = os.path.join(INPUT_DIR, filename)
        try:
            # utf-8-sig strips the byte-order mark Excel adds when saving CSV,
            # which would otherwise corrupt the first column's name.
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    print(f"  {filename}: no header row found, skipping")
                    continue

                mapping = detect_columns(reader.fieldnames)
                if not mapping:
                    print(f"  {filename}: could not recognise any columns "
                          f"({', '.join(reader.fieldnames[:6])}). "
                          f"Add a mapping under 'catalog:' in config.yaml.")
                    continue

                label = labels.get(filename, default_label)
                print(f"  {filename} [cited as: {label}] matched columns -> "
                      f"{', '.join(f'{k}={v}' for k, v in mapping.items())}")

                count = 0
                for row in reader:
                    page = row_to_page(row, mapping, label, filename)
                    if page:
                        pages.append(page)
                        count += 1
                print(f"  {filename}: {count} rows loaded")

        except Exception as e:
            # One malformed file must not lose the rows already read.
            print(f"  {filename}: failed to read ({e})")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for page in pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")

    print(f"Loaded {len(pages)} catalog entries from {len(csv_files)} file(s) "
          f"-> {OUTPUT_FILE}")
    return pages


if __name__ == "__main__":
    load_catalog_csvs()
