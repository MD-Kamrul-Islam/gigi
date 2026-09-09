# Gigi — ISU College of Business concierge chatbot

A chatbot for Graduate Business Birds (GBB) that points College of Business
students to the exact page answering their question. Built to run for under
$10/month.

**New here? Read `ARCHITECTURE.md`** — it explains how the system works and
where to change things.

## Setup

```
pip install -r requirements.txt
copy _env.example .env          # then paste your OpenAI key into .env
```

Load the key into your terminal (PowerShell):
```powershell
$env:OPENAI_API_KEY = (Get-Content .env | Select-String "OPENAI_API_KEY" | ForEach-Object { $_.Line.Split("=",2)[1] })
```

## Build the knowledge base

```
python ingest.py
```

Crawls every site listed in `config.yaml`, loads your `manual_docs/` files,
chunks everything, and embeds it. Takes a few minutes; costs a few cents.
Re-run whenever you change sources or add documents.

## Use it

```
python chat_cli.py "what student organizations can I join?"   # terminal chat
python query.py "student organizations"                        # raw retrieval, for debugging
uvicorn app:app --reload                                       # web app at http://127.0.0.1:8000
```

## Change what Gigi knows

- **Add a website**: add a block under `sources:` in `config.yaml`, re-ingest.
  A commented Career Services example is already in the file.
- **Add your own content**: drop `.md`/`.txt` files in `manual_docs/`, re-ingest.
- **Change her personality**: edit `persona` in `settings.json` (admin
  dashboard coming in Phase 3b). No re-ingest, no redeploy.

See the "I want to change X" table in `ARCHITECTURE.md` for everything else.

## Deploying to Railway

The `Procfile` is ready. Set `OPENAI_API_KEY` as a Railway environment
variable — never commit it. Run `ingest.py` before or after deploy so the
index exists.

## Files you'll touch

`config.yaml` (sources and tuning) · `manual_docs/` (your content) ·
`settings.json` (personality) · `feedback.jsonl` (what students reported)
