# How Gigi works

Written so you can change things confidently without reverse-engineering the
code. Read the first section once; use the rest as a lookup table.

---

## The one-paragraph version

Gigi is a **retrieval-augmented chatbot**. She has no built-in knowledge of
ISU. A crawler copies web pages into a local file, those pages are cut into
small pieces, and each piece is turned into a list of numbers ("embedding")
that captures its meaning. When a student asks something, the question is
turned into numbers the same way, the closest pieces are found by comparing
numbers, and those pieces are handed to an OpenAI model with the instruction:
*answer using only this text*. That last constraint is why she doesn't make
things up — she is quoting from pages you chose, not from memory.

---

## The journey of one question

```
student types a question
        │
        ▼
[1] REWRITE      gigi_chat.rewrite_query()
                 "how about for STEM?"  ->  "STEM MBA graduate assistantships"
                 Only runs when there's conversation history. Cheap model.
        │
        ▼
[2] RETRIEVE     vectorstore.search()
                 Finds ~20 candidate chunks using two signals:
                   • embedding similarity (meaning)
                   • keyword overlap (literal words — rescues answers buried
                     inside long list pages)
                 Then caps chunks per page so results stay diverse, and pulls
                 the chunks either side of the best hit (neighbour expansion).
        │
        ▼
[3] RERANK       gigi_chat.rerank()
                 Cheap model picks which candidates actually ANSWER the
                 question, vs. merely being on the same topic. Keeps top 8.
        │
        ▼
[4] ANSWER       gigi_chat.answer_question()
                 Chat model gets: persona (from settings.json) + the chunks +
                 the question. Writes 2–5 sentences plus links.
        │
        ▼
[5] CONFIDENCE   gigi_chat.confidence_label()
                 high/medium/low from how strongly the best chunk matched.
                 NOT the model's opinion of itself — models are overconfident
                 and poorly calibrated about their own accuracy.
        │
        ▼
answer + sources + confidence  ->  browser
```

Steps 1 and 3 can be switched off in `config.yaml` (`rewrite_followups`,
`rerank`) if you ever want to trade a little accuracy for lower cost. If
either step's API call fails, it logs a warning and continues — a failed
optimisation never takes Gigi down.

---

## What each file does

**Build the knowledge base** (run occasionally, costs a few cents)

| File | Job |
|---|---|
| `config.yaml` | Which sites to crawl, models, all tuning knobs. **You edit this.** |
| `settings.py` | Loads config.yaml + settings.json. Every module reads settings from here. |
| `scraper.py` | Visits each configured site, saves page text → `pages.jsonl` |
| `manual_docs.py` | Loads your `.md`/`.txt` files → `manual_pages.jsonl` |
| `chunker.py` | Cuts pages into overlapping chunks, strips nav boilerplate → `chunks.jsonl` |
| `vectorstore.py` | Embeds chunks → `vector_index.npz`; also does search at question time |
| `ingest.py` | Runs all four in order. **This is the command you actually run.** |

**Answer questions** (runs constantly)

| File | Job |
|---|---|
| `gigi_chat.py` | The pipeline above: rewrite, rerank, answer, confidence |
| `app.py` | Web server and API endpoints |
| `static/index.html` | The entire student interface (HTML + CSS + JS in one file) |
| `query.py` | Command-line retrieval tester — shows raw chunks, no LLM |
| `chat_cli.py` | Command-line chat — full answers, no browser needed |

**Data files** (generated; safe to delete and rebuild)

`pages.jsonl` · `manual_pages.jsonl` · `chunks.jsonl` · `vector_index.npz` ·
`feedback.jsonl` (student ratings) · `usage.json` (daily counter) ·
`settings.json` (persona and welcome copy)

---

## "I want to change X"

| Goal | Where | Re-ingest? |
|---|---|---|
| Add a website (Career Services, etc.) | `config.yaml` → `sources:` — copy the commented example | Yes |
| Stop crawling a section | that source's `blocked_prefixes` | Yes |
| Crawl more/fewer pages of a site | that source's `max_pages` | Yes |
| Add your own content (RSO list, career advice) | drop `.md`/`.txt` in `manual_docs/` | Yes |
| Change Gigi's personality or tone | `settings.json` → `persona` (admin dashboard in 3b) | No |
| Change welcome text or suggested chips | `settings.json` | No |
| More/less context per answer | `config.yaml` → `retrieval.top_k` | No |
| Answers feel cut off | `config.yaml` → `limits.max_answer_tokens` | No |
| Confidence labels feel wrong | `config.yaml` → `confidence.high` / `.medium` | No |
| Use a better (pricier) model | `config.yaml` → `models.chat` | No |
| Better embeddings | `models.embedding: text-embedding-3-large` | **Yes** |
| Protect the budget harder | `limits.daily_question_cap` | No |

Rule of thumb: **anything that changes stored text needs a re-ingest;
anything that changes behaviour at question time doesn't.**

---

## Costs, and why this stays under $10/month

Per student question, Gigi makes up to four API calls: embed the question,
rewrite (only on follow-ups), rerank, and write the answer. All of them run on
OpenAI's budget tier by default.

Rough shape at pilot scale: a question costs a fraction of a cent, so even a
few thousand questions a month sits in the low single dollars. The one-off
cost is embedding the site during ingest — cents per full rebuild, so
re-ingesting weekly is not a budget concern.

Two safeguards, because the API has no hard spend stop:
1. `limits.daily_question_cap` in `config.yaml` — Gigi politely refuses past it.
2. Your OpenAI **prepaid balance** is the real ceiling. Keep a small balance
   rather than relying on the dashboard's "limit" setting, which may only
   alert. Verify this behaviour when you deploy; it has changed before.

If you upgrade `models.chat` to a larger model, re-do this maths first — the
chat model is where nearly all the cost lives.

---

## Things that are deliberate

- **Local `.npz` index, not a vector database.** At a few thousand chunks,
  brute-force numpy search takes milliseconds. A hosted vector DB would add a
  monthly bill and an outage source for zero benefit at this scale.
- **`.jsonl` files everywhere.** You can open any of them in a text editor and
  see exactly what Gigi knows. Debuggability beats elegance for a pilot.
- **Persona in `settings.json`, not code.** Tone is the thing you'll iterate on
  most; it should never require a redeploy.
- **The accuracy rules inside the persona are load-bearing.** When editing tone
  from the dashboard, keep the ACCURACY RULES block. Those sentences are the
  difference between a concierge and a confident liar.
- **Confidence is grounding-based.** Asking a model how sure it is produces
  confident-sounding noise; retrieval strength is a real measurement.

---

## When something goes wrong

| Symptom | Likely cause |
|---|---|
| "OPENAI_API_KEY not set" | Key missing from environment / Railway variables |
| "vector_index.npz not found" | Never ran `python ingest.py` |
| Gigi says she can't find things she should know | Content missing from the crawl — check `pages.jsonl`, or add a manual doc |
| Answers cite the wrong office | A source's `label` in `config.yaml` is unclear |
| Everything is "low" confidence | Thresholds too high for your content — lower them in `config.yaml` |
| `IndentationError` after editing | Notepad broke the indentation — use VS Code for Python files |
