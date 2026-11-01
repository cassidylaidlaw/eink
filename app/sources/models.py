"""Wind from iWindsurf's model forecasts, for days the pro forecast doesn't cover.

Each value is the spread across several models (lowest to highest predicted
average wind), so a wide range means the models disagree.
"""

import asyncio
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta

from ..config import CFG, TZ

log = logging.getLogger(__name__)

# 1-6 pm grid: iWindsurf's Bay-tuned 1 km WRF and NOAA's HRRR (~1.5 days).
# WRF tracked the live afternoon breeze best when compared on 2026-11-01;
# NAM/RRFS/HRRR brought it in hours late, so a 4-model spread was too wide.
GRID_MODELS = {211: "WRF 1km", 170: "HRRR"}
# Outlook: high-res models only (WRF, HRRR, NAM 3km, RRFS reach 1.5-3 days).
# Global models (BLEND, ECMWF, GFS) can't resolve the sea breeze - they
# forecast 2-4 kts Bay-wide on breezy days - so days beyond the high-res
# models' reach are left out rather than filled with them.
OUTLOOK_MODELS = {211: "WRF 1km", 170: "HRRR", 161: "NAM 3km", 280: "RRFS"}
MIN_MODELS = 2  # a range needs at least two models
REFRESH = 3600  # model runs land hourly at best

_cache: tuple[float, dict] | None = None


async def _series(api, client, spot_id: int, model_id: int) -> dict[datetime, tuple[float, str]]:
    """{local hour: (avg wind kts, direction)} from the newest run of one model."""
    try:
        r = await api(client, "model/getModelDataBySpot", spot_id=spot_id, model_id=model_id)
    except Exception as e:
        log.warning("model %s for spot %s failed: %s", model_id, spot_id, e)
        return {}
    best: dict[datetime, tuple[str, float, str]] = {}
    for row in r.get("model_data") or []:
        if row.get("wind_speed") is None:
            continue
        t = datetime.fromisoformat(row["model_time_local"]).astimezone(TZ).replace(tzinfo=None)
        run = row.get("model_run_time_utc", "")
        if t not in best or run > best[t][0]:  # several runs overlap; keep the newest
            best[t] = (run, row["wind_speed"], row.get("wind_dir_txt") or "")
    return {t: (v, d) for t, (_, v, d) in best.items()}


async def fetch(api, client) -> dict:
    """{spot_id: {model_id: series}} for the configured spots, refreshed hourly."""
    global _cache
    if _cache and time.time() - _cache[0] < REFRESH:
        return _cache[1]
    spots = CFG["wind"]["spots"]
    outlook_spots = {s["id"] for s in spots if s["name"] in CFG["wind"].get("model_outlook_spots", [])}
    jobs = [(s["id"], m) for s in spots
            for m in (GRID_MODELS.keys() | (OUTLOOK_MODELS.keys() if s["id"] in outlook_spots else set()))]
    sem = asyncio.Semaphore(4)

    async def one(sid, m):
        async with sem:
            return sid, m, await _series(api, client, sid, m)

    data: dict[int, dict[int, dict]] = {}
    for sid, m, series in await asyncio.gather(*(one(*j) for j in jobs)):
        data.setdefault(sid, {})[m] = series
    _cache = (time.time(), data)
    return data


def _spread(series_list, hours: list[datetime]):
    """Per hour: (low, high, direction) across models, or None with <2 models."""
    out = []
    for h in hours:
        vals = [s[h] for s in series_list if h in s]
        if len(vals) < MIN_MODELS:
            out.append(None)
            continue
        speeds = [v for v, _ in vals]
        # Direction from the first model in priority order that has one.
        direction = next((d for _, d in vals if d), "")
        out.append((round(min(speeds)), round(max(speeds)), direction))
    return out


def grid(data: dict, spot_id: int, day: date, hours: list[int]):
    series = [data.get(spot_id, {}).get(m, {}) for m in GRID_MODELS]
    return _spread(series, [datetime.combine(day, datetime.min.time()) + timedelta(hours=h) for h in hours])


def outlook(data: dict, first_day: date, days: int, hours: list[int]) -> list[dict]:
    """One line per day: afternoon range at each outlook spot, e.g. 'Berkeley 8-12 WSW · TI 12-16 W'."""
    names = {s["id"]: s["name"] for s in CFG["wind"]["spots"]}
    outlook_ids = {s["id"] for s in CFG["wind"]["spots"] if s["name"] in CFG["wind"].get("model_outlook_spots", [])}
    lines = []
    for i in range(days):
        day = first_day + timedelta(days=i)
        parts = []
        for sid, models in data.items():
            if sid not in outlook_ids:
                continue
            cells = _spread([models.get(m, {}) for m in OUTLOOK_MODELS],
                            [datetime.combine(day, datetime.min.time()) + timedelta(hours=h) for h in hours])
            cells = [c for c in cells if c]
            if not cells:
                continue
            lo, hi = min(c[0] for c in cells), max(c[1] for c in cells)
            direction = Counter(c[2] for c in cells if c[2]).most_common(1)
            parts.append(f"{names[sid]} {lo if lo == hi else f'{lo}–{hi}'}{' ' + direction[0][0] if direction else ''}")
        if parts:
            lines.append({"day": f"{day:%a}", "text": " · ".join(parts)})
    return lines
