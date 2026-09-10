"""
Gigi's web server -- what Railway runs.

Student endpoints:
  GET  /                 the chat page (static/index.html)
  GET  /api/welcome      welcome text + suggested question chips (from settings)
  POST /api/chat         ask a question
  POST /api/feedback     thumbs up/down, with optional "what was wrong"
  GET  /health           health check for Railway

The admin dashboard (docs upload, persona editor, feedback review, rebuild
button) is Phase 3b and mounts onto this same app.

Cost safety: a daily question cap from config.yaml. OpenAI has no hard spend
stop, so this is the real guard on the monthly ceiling.
"""

import json
import os
from datetime import date, datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gigi_chat import answer_question
from settings import cfg, load_settings

FEEDBACK_FILE = "feedback.jsonl"
USAGE_FILE = "usage.json"
STATIC_DIR = "static"

app = FastAPI(title="Gigi", docs_url=None, redoc_url=None)

# Admin dashboard API (password-protected in admin.py).
app.include_router(admin_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # pilot has no auth; tighten if API and UI split hosts
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict] = Field(default_factory=list, max_length=20)


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=4000)
    rating: str = Field(pattern="^(up|down)$")
    comment: str = Field(default="", max_length=1000)


def _check_and_count_question() -> bool:
    """Simple per-day counter so a runaway loop or a bored student can't burn
    the month's budget. Returns False when today's cap is reached."""
    cap = int(cfg("limits", "daily_question_cap", 1500))
    today = date.today().isoformat()
    usage = {"date": today, "count": 0}
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("date") == today:
                usage = saved
        except Exception:
            pass
    if usage["count"] >= cap:
        return False
    usage["count"] += 1
    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(usage, f)
    except Exception as e:
        print(f"[warn] could not write usage file: {e}")
    return True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/welcome")
def welcome():
    """Welcome copy and suggested chips come from settings.json so the admin
    dashboard can change them without a redeploy."""
    s = load_settings()
    return {
        "title": s.get("welcome_title", "Hi, I'm Gigi"),
        "message": s.get("welcome_message", ""),
        "suggestions": s.get("suggested_questions", []),
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not _check_and_count_question():
        return JSONResponse(status_code=429, content={
            "answer": "I've hit my question limit for today — I'll be back "
                      "tomorrow. For anything urgent, the COB Advisement "
                      "Center in SFHB 129 can help.",
            "confidence": "low", "sources": [], "error": True,
        })
    try:
        return answer_question(req.question.strip(), history=req.history)
    except Exception as e:
        print(f"[error] /api/chat failed: {type(e).__name__}: {e}")
        return JSONResponse(status_code=500, content={
            "answer": "Chirp… my wings got tangled for a second. "
                      "Try that again in a moment.",
            "confidence": "low", "sources": [], "error": True,
        })


@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    """Every rating is one line in feedback.jsonl. Thumbs-down entries are the
    queue the admin dashboard reviews to write corrections (Phase 3c)."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": req.question,
        "answer": req.answer,
        "rating": req.rating,
        "comment": req.comment,
        "status": "new" if req.rating == "down" else "noted",
    }
    try:
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[error] could not save feedback: {e}")
        return JSONResponse(status_code=500, content={"saved": False})
    return {"saved": True}


# Serve the chat UI. Mounted last so /api/* routes always win.
if os.path.isdir(STATIC_DIR):
    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
