"""
Loads Gigi's configuration. Every other module imports from here, so there
is exactly one place that knows where settings come from.

Two layers, on purpose:

  config.yaml   -- structural config: which sites to crawl, models, retrieval
                   knobs, limits. You edit this in a text editor. Changing it
                   usually means re-ingesting.

  settings.json -- things the admin dashboard edits at runtime: Gigi's
                   persona/system prompt, the welcome message, and the
                   suggested question chips. Written by the admin panel, so
                   tone changes never need a code change or redeploy.
                   Created automatically with sane defaults on first run.

If config.yaml is missing or malformed, we fall back to DEFAULT_CONFIG rather
than crashing -- a broken config should never take Gigi offline.
"""

import copy
import json
import os

import yaml

CONFIG_FILE = os.environ.get("GIGI_CONFIG", "config.yaml")
SETTINGS_FILE = os.environ.get("GIGI_SETTINGS", "settings.json")


# --------------------------------------------------------------------------
# Fallback config -- mirrors config.yaml. Used only if the file is unreadable.
# --------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "sources": [{
        "label": "College of Business",
        "start_url": "https://business.illinoisstate.edu/",
        "allowed_domain": "business.illinoisstate.edu",
        "path_prefix": "",
        "blocked_prefixes": ["/alumni", "/faculty-staff"],
        "max_pages": 500,
    }],
    "crawl": {"request_delay_seconds": 0.5, "timeout_seconds": 10, "min_page_chars": 50},
    "chunking": {"chunk_size": 800, "chunk_overlap": 150, "boilerplate_threshold": 0.35},
    "models": {"embedding": "text-embedding-3-small", "chat": "gpt-5.6-luna",
               "utility": "gpt-5.6-luna"},
    "retrieval": {"candidates": 20, "top_k": 8, "max_per_url": 3,
                  "keyword_weight": 0.12, "neighbor_expansion": True,
                  "rerank": True, "rewrite_followups": True},
    "confidence": {"high": 0.62, "medium": 0.50},
    "limits": {"max_question_chars": 1000, "max_answer_tokens": 350,
               "max_history_turns": 6, "daily_question_cap": 1500},
}


# --------------------------------------------------------------------------
# Gigi's personality. This is the DEFAULT only -- the live copy lives in
# settings.json and is edited from the admin dashboard.
#
# The rules below are not decoration: each one exists because of something
# observed in testing. Keep the ACCURACY RULES intact when editing tone.
# --------------------------------------------------------------------------
DEFAULT_PERSONA = """\
You are Gigi, the friendly concierge chatbot for the Graduate Business Birds \
(GBB) club, helping Illinois State University College of Business students \
find the right resource fast.

WHO YOU ARE
- You're a bird -- a playful, energetic, early-20s-college-vibe Redbird. Not \
gendered.
- Light personality, used sparingly: at most ONE touch per reply -- a bird pun \
("let me swoop in and grab that"), a chirpy interjection ("Chirp! Found it"), \
or a single feather emoji. Never stack them.

HOW YOU ANSWER
- You are a concierge, NOT an advisor: a short, accurate answer plus the link \
to the exact resource. Not deep advice, not long explanations.
- Keep answers to 2-5 short sentences plus the link(s). Plain text; use a list \
only when naming 3+ items.
- Always include the source link(s) you actually used, as plain URLs.

ACCURACY RULES (never relax these)
- Answer ONLY from the CONTEXT provided. Never use outside knowledge, never \
guess, never fill gaps from memory.
- If the context doesn't answer the question, say so plainly ("I couldn't find \
that one on the College of Business site") and point to who can help -- the COB \
Advisement Center (SFHB 129) for academic questions, or an office named in the \
context. Never offer a partial guess instead.
- When a fact comes from one specific program's page (for example an \
assistantship policy on the Accountancy master's page), name that program. \
Never present program-specific details as college-wide.
- Each source is labeled with the office it came from. When sources come from \
different offices, say which office you're citing.
- If the question isn't about ISU, the College of Business, or student life \
around it, give a one-line playful redirect: "I only fly around the ISU Quad -- \
try me on business school stuff!"
- Accuracy beats personality every time. When in doubt, be plain and correct.
"""

DEFAULT_SETTINGS = {
    "persona": DEFAULT_PERSONA,
    "welcome_title": "Hi, I'm Gigi",
    "welcome_message": (
        "Ask me anything about the Illinois State College of Business — "
        "programs, scholarships, advising, student orgs. I'll point you "
        "straight to the right page."
    ),
    "suggested_questions": [
        "What student organizations can I join?",
        "What scholarships are available?",
        "How do I contact an academic advisor?",
        "What's the difference between the MBA and STEM MBA?",
    ],
}


_config_cache: dict | None = None


def load_config(force_reload: bool = False) -> dict:
    """Structural config from config.yaml, with defaults filled in for any
    missing section so a partial file still works."""
    global _config_cache
    if _config_cache is not None and not force_reload:
        return _config_cache

    config = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for key, value in loaded.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)   # merge section, keep defaults
            else:
                config[key] = value
    except FileNotFoundError:
        print(f"[settings] {CONFIG_FILE} not found -- using built-in defaults.")
    except Exception as e:
        print(f"[settings] could not read {CONFIG_FILE} ({e}) -- using defaults.")

    _config_cache = config
    return config


def load_settings() -> dict:
    """Runtime settings (persona, welcome text, chips). Creates the file with
    defaults on first run so the admin panel always has something to edit."""
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
        except Exception as e:
            print(f"[settings] {SETTINGS_FILE} unreadable ({e}) -- using defaults.")
    else:
        save_settings(settings)
    return settings


def save_settings(settings: dict) -> dict:
    """Persist runtime settings. Used by the admin dashboard (Phase 3b)."""
    current = copy.deepcopy(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                current.update(json.load(f))
        except Exception:
            pass
    current.update(settings)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current


# Convenience accessors so callers read naturally: cfg("retrieval", "top_k")
def cfg(section: str, key: str, default=None):
    return load_config().get(section, {}).get(key, default)


def sources() -> list[dict]:
    return load_config().get("sources", [])
