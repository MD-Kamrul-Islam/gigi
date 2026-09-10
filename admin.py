"""
Admin dashboard API — everything you'd otherwise have to do by editing files
on a server: change Gigi's personality, upload documents, review what students
reported, and rebuild the knowledge base.

Security: one shared password, set as the ADMIN_PASSWORD environment variable
(in Railway, alongside OPENAI_API_KEY). The browser sends it as a bearer token
on every admin request. That's deliberately simple — the pilot has no user
accounts, and this protects the one thing that needs protecting: write access
to what Gigi says and knows.

If ADMIN_PASSWORD is not set, the admin routes refuse everything rather than
defaulting to open. A dashboard that silently has no password is worse than
one that's unavailable.
"""

import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from settings import DEFAULT_PERSONA, load_config, load_settings, save_settings

router = APIRouter(prefix="/api/admin")

MANUAL_DIR = "manual_docs"
FEEDBACK_FILE = "feedback.jsonl"
USAGE_FILE = "usage.json"
ALLOWED_EXTENSIONS = (".md", ".txt")
MAX_UPLOAD_BYTES = 2_000_000

# Rebuild state, shared across requests. The rebuild runs in a background
# thread so the dashboard stays responsive during a multi-minute crawl.
_rebuild = {"running": False, "started": None, "finished": None,
            "ok": None, "log": []}
_rebuild_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def require_admin(authorization: str = Header(default="")):
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if not expected:
        raise HTTPException(503, "Admin dashboard is disabled: ADMIN_PASSWORD "
                                 "is not set on the server.")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else authorization
    if supplied != expected:
        raise HTTPException(401, "Wrong password.")
    return True


@router.post("/login")
def login(_: bool = Depends(require_admin)):
    """The browser calls this to check a password before showing the dashboard."""
    return {"ok": True}


# ---------------------------------------------------------------------------
# Gigi's personality and welcome screen
# ---------------------------------------------------------------------------
class SettingsPayload(BaseModel):
    persona: str = Field(min_length=50, max_length=20000)
    welcome_title: str = Field(min_length=1, max_length=80)
    welcome_message: str = Field(max_length=500)
    suggested_questions: list[str] = Field(default_factory=list, max_length=6)


@router.get("/settings")
def get_settings(_: bool = Depends(require_admin)):
    s = load_settings()
    return {**s, "default_persona": DEFAULT_PERSONA}


@router.put("/settings")
def put_settings(payload: SettingsPayload, _: bool = Depends(require_admin)):
    """Saves immediately — changes affect the next question, no redeploy.

    We warn (but never block) if the accuracy rules look like they've been
    edited out. Those lines are what keep Gigi from inventing answers, and
    it's easy to delete them by accident while rewriting tone.
    """
    data = payload.model_dump()
    data["suggested_questions"] = [q.strip() for q in data["suggested_questions"] if q.strip()]
    saved = save_settings(data)

    lowered = data["persona"].lower()
    warnings = []
    if "context" not in lowered:
        warnings.append("Your persona no longer tells Gigi to answer only from "
                        "the CONTEXT. She may start making things up.")
    if "advisement" not in lowered and "advisor" not in lowered:
        warnings.append("No fallback contact mentioned — Gigi may not know "
                        "where to send students when she can't find an answer.")
    return {"saved": True, "warnings": warnings, "settings": saved}


@router.post("/settings/reset-persona")
def reset_persona(_: bool = Depends(require_admin)):
    """Restore the tested default personality if an edit goes wrong."""
    save_settings({"persona": DEFAULT_PERSONA})
    return {"persona": DEFAULT_PERSONA}


# ---------------------------------------------------------------------------
# Manual documents
# ---------------------------------------------------------------------------
def _safe_name(filename: str) -> str:
    """Strip any path and unusual characters — an uploaded name must never be
    able to write outside manual_docs/."""
    base = os.path.basename(filename or "").strip()
    base = re.sub(r"[^A-Za-z0-9._-]", "-", base)
    if not base.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(400, "Only .md and .txt files can be uploaded.")
    if base.startswith("."):
        raise HTTPException(400, "Invalid file name.")
    return base


@router.get("/docs")
def list_docs(_: bool = Depends(require_admin)):
    os.makedirs(MANUAL_DIR, exist_ok=True)
    docs = []
    for name in sorted(os.listdir(MANUAL_DIR)):
        if name.lower().endswith(ALLOWED_EXTENSIONS):
            path = os.path.join(MANUAL_DIR, name)
            stat = os.stat(path)
            docs.append({
                "name": name,
                "bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
    return {"docs": docs}


@router.get("/docs/{name}")
def read_doc(name: str, _: bool = Depends(require_admin)):
    path = os.path.join(MANUAL_DIR, _safe_name(name))
    if not os.path.exists(path):
        raise HTTPException(404, "File not found.")
    with open(path, "r", encoding="utf-8") as f:
        return {"name": _safe_name(name), "content": f.read()}


class DocPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(max_length=MAX_UPLOAD_BYTES)


@router.put("/docs")
def write_doc(payload: DocPayload, _: bool = Depends(require_admin)):
    """Create or edit a document straight from the browser — no upload needed
    for quick fixes like adding one RSO to the list."""
    os.makedirs(MANUAL_DIR, exist_ok=True)
    name = _safe_name(payload.name)
    with open(os.path.join(MANUAL_DIR, name), "w", encoding="utf-8") as f:
        f.write(payload.content)
    return {"saved": True, "name": name, "needs_rebuild": True}


@router.post("/docs/upload")
async def upload_doc(file: UploadFile = File(...), _: bool = Depends(require_admin)):
    os.makedirs(MANUAL_DIR, exist_ok=True)
    name = _safe_name(file.filename)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File is too large (2 MB limit).")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be plain UTF-8 text.")
    with open(os.path.join(MANUAL_DIR, name), "w", encoding="utf-8") as f:
        f.write(text)
    return {"saved": True, "name": name, "needs_rebuild": True}


@router.delete("/docs/{name}")
def delete_doc(name: str, _: bool = Depends(require_admin)):
    path = os.path.join(MANUAL_DIR, _safe_name(name))
    if os.path.exists(path):
        os.remove(path)
    return {"deleted": True, "needs_rebuild": True}


# ---------------------------------------------------------------------------
# Feedback review
# ---------------------------------------------------------------------------
def _read_feedback() -> list[dict]:
    if not os.path.exists(FEEDBACK_FILE):
        return []
    rows = []
    with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                row = json.loads(line)
                row["id"] = i
                rows.append(row)
            except json.JSONDecodeError:
                continue
    return rows


@router.get("/feedback")
def get_feedback(rating: str = "", status: str = "", _: bool = Depends(require_admin)):
    rows = _read_feedback()
    if rating in ("up", "down"):
        rows = [r for r in rows if r.get("rating") == rating]
    if status:
        rows = [r for r in rows if r.get("status", "new") == status]
    rows.reverse()  # newest first
    return {"feedback": rows[:200], "total": len(rows)}


class StatusPayload(BaseModel):
    id: int
    status: str = Field(pattern="^(new|resolved)$")


@router.put("/feedback/status")
def set_feedback_status(payload: StatusPayload, _: bool = Depends(require_admin)):
    """Mark a reported answer as handled, so your review queue stays short."""
    rows = _read_feedback()
    if not 0 <= payload.id < len(rows):
        raise HTTPException(404, "No such feedback entry.")
    rows[payload.id]["status"] = payload.status
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        for row in rows:
            row.pop("id", None)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"updated": True}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@router.get("/stats")
def stats(_: bool = Depends(require_admin)):
    rows = _read_feedback()
    up = sum(1 for r in rows if r.get("rating") == "up")
    down = sum(1 for r in rows if r.get("rating") == "down")
    unresolved = sum(1 for r in rows if r.get("rating") == "down"
                     and r.get("status", "new") == "new")

    questions_today = 0
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                usage = json.load(f)
            questions_today = usage.get("count", 0)
        except Exception:
            pass

    chunks = 0
    if os.path.exists("chunks.jsonl"):
        with open("chunks.jsonl", "r", encoding="utf-8") as f:
            chunks = sum(1 for _ in f)

    index_built = None
    if os.path.exists("vector_index.npz"):
        index_built = datetime.fromtimestamp(
            os.path.getmtime("vector_index.npz"), timezone.utc).isoformat()

    config = load_config()
    return {
        "thumbs_up": up,
        "thumbs_down": down,
        "unresolved_reports": unresolved,
        "questions_today": questions_today,
        "daily_cap": config.get("limits", {}).get("daily_question_cap"),
        "chunks": chunks,
        "index_built": index_built,
        "sources": [{"label": s.get("label"), "domain": s.get("allowed_domain"),
                     "max_pages": s.get("max_pages")} for s in config.get("sources", [])],
        "docs": len([n for n in os.listdir(MANUAL_DIR)
                     if n.lower().endswith(ALLOWED_EXTENSIONS)])
                if os.path.isdir(MANUAL_DIR) else 0,
    }


# ---------------------------------------------------------------------------
# Rebuild the knowledge base
# ---------------------------------------------------------------------------
def _run_rebuild():
    """Runs ingest.py as a separate process. Separate on purpose: a crawl that
    dies can't take the web server down with it, and we capture its output as
    a log you can read in the dashboard."""
    try:
        process = subprocess.Popen(
            [sys.executable, "ingest.py"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            with _rebuild_lock:
                _rebuild["log"].append(line.rstrip())
                _rebuild["log"] = _rebuild["log"][-400:]  # keep the tail only
        process.wait()
        ok = process.returncode == 0
    except Exception as e:
        with _rebuild_lock:
            _rebuild["log"].append(f"rebuild failed to start: {e}")
        ok = False

    # Drop the cached index so the running server serves the new content
    # without needing a restart.
    try:
        from vectorstore import reset_index_cache
        reset_index_cache()
    except Exception:
        pass

    with _rebuild_lock:
        _rebuild.update(running=False, ok=ok,
                        finished=datetime.now(timezone.utc).isoformat())


@router.post("/rebuild")
def start_rebuild(_: bool = Depends(require_admin)):
    with _rebuild_lock:
        if _rebuild["running"]:
            raise HTTPException(409, "A rebuild is already running.")
        _rebuild.update(running=True, ok=None, log=[],
                        started=datetime.now(timezone.utc).isoformat(),
                        finished=None)
    threading.Thread(target=_run_rebuild, daemon=True).start()
    return {"started": True}


@router.get("/rebuild/status")
def rebuild_status(_: bool = Depends(require_admin)):
    with _rebuild_lock:
        return {k: (v[-40:] if k == "log" else v) for k, v in _rebuild.items()}
