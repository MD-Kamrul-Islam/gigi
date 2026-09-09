"""
Gigi's brain: question in, grounded answer out.

The pipeline, in order:

  1. REWRITE   (optional, cheap)  Follow-ups like "how about for STEM?" are
                                  meaningless to a search engine on their own.
                                  A tiny LLM call turns them into standalone
                                  questions using the conversation so far.
  2. RETRIEVE                     Hybrid vector + keyword search pulls ~20
                                  candidate chunks (see vectorstore.py).
  3. RERANK    (optional, cheap)  The cheap model picks the most genuinely
                                  relevant chunks. Embeddings measure topical
                                  similarity; this measures "does it actually
                                  answer the question", which is why an awards
                                  page stops outranking the real listing.
  4. ANSWER                       The chat model writes the reply using ONLY
                                  those chunks, following the persona from
                                  settings.json (admin-editable).
  5. CONFIDENCE                   high/medium/low from retrieval strength --
                                  not the model's opinion of itself, which is
                                  not reliably calibrated.

Every step except retrieval and answering can be switched off in config.yaml
if you ever want to trade accuracy for cost.
"""

from settings import cfg, load_settings
from vectorstore import get_client, search


def _chat(messages: list[dict], model: str, max_tokens: int,
          temperature: float = 0.4) -> str:
    client = get_client()
    response = client.chat.completions.create(
        model=model, messages=messages,
        max_completion_tokens=max_tokens)
    return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Step 1: make follow-up questions searchable on their own
# ---------------------------------------------------------------------------
REWRITE_PROMPT = """\
Rewrite the student's latest message as a standalone search query for a search \
over Illinois State University College of Business web pages.

Rules:
- Resolve pronouns and references using the conversation ("how about for STEM?" \
after MBA talk becomes "STEM MBA graduate assistantships").
- Keep the student's own keywords; do not invent topics they didn't mention.
- If the message is already standalone, return it unchanged.
- Return ONLY the query text, nothing else."""


def rewrite_query(question: str, history: list[dict]) -> str:
    if not history or not cfg("retrieval", "rewrite_followups", True):
        return question
    recent = [t for t in history[-4:] if t.get("role") in ("user", "assistant")]
    if not recent:
        return question

    transcript = "\n".join(
        f"{t['role']}: {str(t.get('content',''))[:400]}" for t in recent)
    try:
        rewritten = _chat(
            [{"role": "system", "content": REWRITE_PROMPT},
             {"role": "user",
              "content": f"Conversation so far:\n{transcript}\n\nLatest message: {question}"}],
            model=cfg("models", "utility", "gpt-5.6-luna"),
            max_tokens=60, temperature=0)
        # Guard against a rewrite that drops the question entirely.
        if 3 <= len(rewritten) <= 300:
            return rewritten
    except Exception as e:
        print(f"[warn] query rewrite failed, using original: {e}")
    return question


# ---------------------------------------------------------------------------
# Step 3: let the cheap model choose which chunks actually answer the question
# ---------------------------------------------------------------------------
RERANK_PROMPT = """\
You rank search results. Given a question and numbered passages, return the \
numbers of the passages that could help answer it, best first, comma-separated \
(e.g. "4,1,7"). Judge whether a passage contains information answering the \
question -- not whether it is on a similar topic. Return at most {k} numbers, \
and nothing but numbers and commas."""


def rerank(question: str, results: list[dict], k: int) -> list[dict]:
    if not cfg("retrieval", "rerank", True) or len(results) <= k:
        return results[:k]

    listing = "\n\n".join(
        f"[{i+1}] {r['title']} ({r['source']})\n{r['text'][:500]}"
        for i, r in enumerate(results))
    try:
        raw = _chat(
            [{"role": "system", "content": RERANK_PROMPT.format(k=k)},
             {"role": "user", "content": f"Question: {question}\n\nPassages:\n{listing}"}],
            model=cfg("models", "utility", "gpt-5.6-luna"),
            max_tokens=40, temperature=0)
        order = []
        for part in raw.replace(" ", "").split(","):
            if part.isdigit():
                i = int(part) - 1
                if 0 <= i < len(results) and i not in order:
                    order.append(i)
        if order:
            picked = [results[i] for i in order[:k]]
            # Backfill from the original ranking if the model returned few.
            for r in results:
                if len(picked) >= k:
                    break
                if r not in picked:
                    picked.append(r)
            return picked
    except Exception as e:
        print(f"[warn] rerank failed, using vector order: {e}")
    return results[:k]


# ---------------------------------------------------------------------------
# Step 5: grounding-based confidence
# ---------------------------------------------------------------------------
def confidence_label(results: list[dict]) -> str:
    if not results:
        return "low"
    top = max(r["score"] for r in results)
    if top >= float(cfg("confidence", "high", 0.62)):
        return "high"
    if top >= float(cfg("confidence", "medium", 0.50)):
        return "medium"
    return "low"


def _format_context(results: list[dict]) -> str:
    return "\n\n".join(
        f"[SOURCE {i}] {r['title']}\nOffice: {r['source']}\nURL: {r['url']}\n{r['text']}"
        for i, r in enumerate(results, 1))


def answer_question(question: str, history: list[dict] | None = None) -> dict:
    """Full pipeline. `history` is prior turns as
    [{"role": "user"|"assistant", "content": str}, ...]."""
    history = history or []
    settings = load_settings()

    search_query = rewrite_query(question, history)
    candidates = search(search_query)
    results = rerank(search_query, candidates, int(cfg("retrieval", "top_k", 8)))

    messages = [{"role": "system", "content": settings["persona"]}]
    max_turns = int(cfg("limits", "max_history_turns", 6))
    for turn in history[-max_turns:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"],
                             "content": str(turn["content"])[:2000]})
    messages.append({"role": "user",
                     "content": f"CONTEXT:\n\n{_format_context(results)}\n\n"
                                f"STUDENT QUESTION: {question}"})

    answer = _chat(
        messages,
        model=cfg("models", "chat", "gpt-5.6-luna"),
        max_tokens=int(cfg("limits", "max_answer_tokens", 350))
    )

    if not answer:
        answer = _chat(
            messages,
            model=cfg("models", "chat", "gpt-5.6-luna"),
            max_tokens=int(cfg("limits", "max_answer_tokens", 350))
        )

    seen, sources = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            sources.append({"title": r["title"], "source": r["source"],
                            "url": r["url"], "score": round(r["score"], 3)})

    return {
        "answer": answer,
        "confidence": confidence_label(results),
        "sources": sources[:3],
        "search_query": search_query,  # shown in admin/debug, not to students
    }
