"""Wind: iWindsurf live readings, pro spot forecast, and summarized outlook.

iWindsurf's site is a front end for WeatherFlow's JSON API. A signed-in page
embeds a session token (`var token = '...'`) that the API accepts as
`wf_token`, so we sign in like a browser, read the token, then call the API.
"""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta

import httpx

from ..config import CFG, TZ, env, is_evening
from . import models, outlook

log = logging.getLogger(__name__)

API = "https://api.weatherflow.com/wxengine/rest/"
LOGIN = "https://secure.iwindsurf.com/?app=wx&rd=profile"
UNITS = {"units_wind": "kts", "units_temp": "f", "units_distance": "mi",
         "units_precip": "in", "units_height": "ft"}
FORECAST_EVERY = 1800
LIVE_MAX_AGE = 45 * 60  # older station readings are shown as missing

_token: str | None = None
_login_lock = asyncio.Lock()
_forecast: tuple[float, dict] | None = None


class AuthError(Exception):
    pass


async def _login(c: httpx.AsyncClient) -> str:
    user, pw = env("IWINDSURF_USER"), env("IWINDSURF_PASS")
    if not (user and pw):
        raise AuthError("IWINDSURF_USER / IWINDSURF_PASS not set")
    # The login page issues a session token cookie; posting credentials
    # upgrades that same token to a signed-in one on the server.
    await c.get(LOGIN, follow_redirects=False)
    await c.post(LOGIN, follow_redirects=False,
                 data={"isun": user, "ispw": pw, "iwok.x": "Sign In", "app": "wx", "rd": "profile"})
    token = c.cookies.get("wfToken", domain=".iwindsurf.com")
    if not token:
        raise AuthError("no session token after sign-in")
    r = await c.get(API + "profile/getProfiles", params={"wf_token": token})
    if not r.json().get("profiles"):
        raise AuthError("sign-in failed (check IWINDSURF_USER / IWINDSURF_PASS)")
    log.info("signed in to iWindsurf")
    return token


async def _api(c: httpx.AsyncClient, path: str, **params) -> dict:
    """Call the API, signing in (again) if there is no token or it was rejected.

    Calls run concurrently (model fetches), so sign-in is serialized: when a
    token expires, one caller signs in and the rest reuse the new token.
    Parallel sign-ins replace each other's sessions and all fail."""
    global _token
    for attempt in (1, 2):
        async with _login_lock:
            if not _token:
                _token = await _login(c)
            token = _token
        r = await c.get(API + path, params={**UNITS, **params, "wf_token": token})
        body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/")) else {}
        status = body.get("status", {}).get("status_code", r.status_code)
        if r.status_code == 200 and status == 0:
            return body
        log.warning("%s returned %s %s; re-authenticating", path, r.status_code,
                    body.get("status", {}).get("status_message"))
        async with _login_lock:
            if _token == token:  # nobody has refreshed it yet
                _token = None
    raise AuthError(f"{path} failed after re-login")


def _live(spots_json: dict) -> dict[int, dict]:
    out = {}
    now = time.time()
    for s in spots_json.get("spots", []):
        names = s.get("data_names", [])
        for st in s.get("stations", []):
            for row in st.get("data_values") or []:
                v = dict(zip(names, row))
                if v.get("avg") is None or not v.get("dir_text"):
                    continue
                ts = datetime.fromisoformat(v["utc_timestamp"] + "+00:00")
                if now - ts.timestamp() > LIVE_MAX_AGE:
                    continue
                out[s["spot_id"]] = {"avg": round(v["avg"]), "gust": round(v["gust"] or v["avg"]),
                                     "dir": v["dir_text"]}
                break
    return out


def _hour_of(label: str) -> int:
    """'8A' -> 8, '12P' -> 12, '1P' -> 13."""
    h, ampm = int(label[:-1]), label[-1].upper()
    return (h % 12) + (12 if ampm == "P" else 0)


def _spot_forecast(fc: dict, spot_id: int, hours: list[int]) -> list[tuple[int, int, str]] | None:
    for s in fc["daily"].get("spot_forecasts", []):
        if s["spot_id"] != spot_id:
            continue
        by_hour = {}
        for block in s["forecast"]:
            for label in block["intervals"]:
                by_hour[_hour_of(label)] = (block["min_speed"], block["max_speed"], block["direction"])
        return [by_hour.get(h) for h in hours]
    return None


def _issued_label(stamp: str | None) -> str:
    """'2026-09-27 19:00:00' -> '7:00p'."""
    try:
        t = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""
    return f"{t.hour % 12 or 12}:{t.minute:02d}{'a' if t.hour < 12 else 'p'}"


def _date_of(stamp: str | None) -> date | None:
    """Leading YYYY-MM-DD of an API timestamp. On days the forecaster doesn't
    publish, the API sends the literal text 'null 00:00:00'."""
    try:
        return datetime.strptime((stamp or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _missing_reason(daily: dict, today: date) -> str:
    """Why there is no current pro forecast, for the display."""
    start, end = _date_of(daily.get("season_start")), _date_of(daily.get("season_end"))
    if start and end and not (start <= today < end):
        nxt = start if today < start else start.replace(year=start.year + 1)
        return f"off-season until {nxt:%b} {nxt.day}"
    if daily.get("season_status") == "forecast not published":
        return "not published today"
    return "no forecast"


async def fetch() -> dict:
    global _forecast
    cfg = CFG["wind"]
    ids = [s["id"] for s in cfg["spots"]]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 (eink dashboard)"}) as c:
        live = _live(await _api(c, "spot/getSpotDetailSetByList", spot_types="1,100,101",
                                spot_list=",".join(map(str, ids))))
        # A forecast failure must never cost the live readings; keep the last one.
        if not _forecast or time.time() - _forecast[0] > FORECAST_EVERY:
            try:
                _forecast = (time.time(), await _api(c, "forecast/getOperationalForecast",
                                                     forecast_id=cfg["forecast_id"]))
            except Exception as e:
                log.warning("pro forecast fetch failed, keeping previous: %s", e)
    fc = _forecast[1] if _forecast else {}
    daily = fc.get("daily") or {}
    hours = cfg["hours"]

    # The pro daily forecast covers one date. The evening update (~7 pm) is for
    # tomorrow, so show today's or a later one. Without a current one (not
    # published, or off-season), fall back to the model spread for the day the
    # board is on: today, or tomorrow after evening_switch.
    now = datetime.now(TZ)
    today = now.date()
    valid = _date_of(daily.get("valid_time_local"))
    pro = valid is not None and valid >= today
    reason = "" if pro else _missing_reason(daily, today)
    grid_day = valid if pro else today + timedelta(days=1 if is_evening(now) else 0)

    model_data = {}
    ext_days = (fc.get("extended") or {}).get("days") or []
    if not pro or not ext_days:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0 (eink dashboard)"}) as c:
                model_data = await models.fetch(_api, c)
        except Exception as e:
            log.warning("model forecast fetch failed: %s", e)

    spots = [{
        "name": s["name"],
        "now": live.get(s["id"]),
        "forecast": (_spot_forecast(fc, s["id"], hours) if pro
                     else models.grid(model_data, s["id"], grid_day, hours) if model_data else None),
    } for s in cfg["spots"]]
    has_grid = any(c for s in spots for c in (s["forecast"] or []))

    if ext_days:
        outlook_lines = await outlook.summarize(ext_days)
        outlook_source = "iWindsurf pro" + (f" · issued {_issued_label(daily.get('issued_timestamp_local'))}"
                                            if pro and daily.get("issued_timestamp_local") else "")
    else:
        outlook_lines = models.outlook(model_data, grid_day + timedelta(days=1), 3, hours) if model_data else []
        outlook_source = "model range: " + ", ".join(models.OUTLOOK_MODELS.values()) if outlook_lines else ""

    day_label = "Today" if grid_day == today else f"{grid_day:%a}"
    return {
        "spots": spots,
        "outlook": outlook_lines,
        "forecast_day": day_label if (pro or has_grid) else "",
        "grid_source": "pro" if pro else ("model" if has_grid else ""),
        "outlook_source": outlook_source,
        "note": reason,
        "placeholder": False,
    }
