"""Condense the forecaster's multi-day outlook into one short line per day.

Calls the OpenAI Responses API only when the source text changes; results
are cached on disk so restarts don't re-spend.
"""

import hashlib
import json
import logging
import re
from datetime import datetime

import httpx

from ..config import CFG, DATA_DIR, env

log = logging.getLogger(__name__)

CACHE = DATA_DIR / "outlook_cache.json"
MAX_CHARS = 62

PROMPT = """You condense a San Francisco Bay wind forecaster's outlook for a small e-ink wall display.

For each day below, write ONE line of at most {max_chars} characters saying how strong
the wind is, where, and when, in the forecaster's own terms.
- The reader sails at {spots}. If the text names one of those, put it first.
  If it names none of them, do NOT mention them: say where the text says the wind is.
- Only use places, strengths, and timing that appear in the text. Never infer that
  a spot gets wind because a nearby one does.
- Keep strength wording like "mid to upper-teens" or ranges like "15-19". No "kts".
- Skip the meteorology (pressure gradients, highs, lows) unless it's the only content;
  then give the gist, e.g. "Weaker; forecast uncertain".
- Abbreviations: TI (Treasure Island), PI (Point Isabel), GG (Golden Gate), PM, AM.

Example: "Mid-upper teens mid-GG to TI, Sherman PM"

Reply with only a JSON array: [{{"day": "Sun", "text": "..."}}, ...] in the same order.

{days}"""


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _fallback(days: list[dict]) -> list[dict]:
    """First sentence, trimmed. Used when there's no API key or the call fails."""
    out = []
    for d in days:
        first = re.split(r"(?<=[.!?])\s", d["summary"].strip(), maxsplit=1)[0]
        if len(first) > MAX_CHARS:
            first = first[:MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
        out.append({"day": d["day"], "text": first})
    return out


async def summarize(extended_days: list[dict]) -> list[dict]:
    days = []
    for d in extended_days[:3]:
        try:
            label = datetime.strptime(d["date_formatted"], "%a, %b %d %Y").strftime("%a")
        except (KeyError, ValueError):
            label = d.get("date_formatted", "")[:3]
        days.append({"day": label, "summary": re.sub(r"\s+", " ", d.get("summary", "")).strip()})
    if not days:
        return []

    key = hashlib.sha1(json.dumps(days).encode()).hexdigest()
    cache = _load()
    if key in cache:
        return cache[key]
    if not env("OPENAI_API_KEY"):
        return _fallback(days)

    prompt = PROMPT.format(
        max_chars=MAX_CHARS,
        spots=", ".join(s["name"] for s in CFG["wind"]["spots"]),
        days="\n\n".join(f"{d['day']}: {d['summary']}" for d in days),
    )
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
                json={"model": env("SUMMARY_MODEL", "gpt-5.6-luna"), "input": prompt},
            )
            r.raise_for_status()
            text = "".join(
                part.get("text", "")
                for item in r.json().get("output", []) if item.get("type") == "message"
                for part in item.get("content", []) if part.get("type") == "output_text"
            )
        lines = json.loads(text[text.index("["):text.rindex("]") + 1])
        result = [{"day": d["day"], "text": str(l.get("text", ""))[:MAX_CHARS + 8]}
                  for d, l in zip(days, lines)]
    except Exception as e:
        log.warning("outlook summary failed, using first sentences: %s", e)
        return _fallback(days)

    log.info("summarized outlook %s", key[:8])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({key: result}))  # only the current one matters
    return result
