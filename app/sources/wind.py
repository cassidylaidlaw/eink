"""Wind: iWindsurf live readings, pro spot forecast, and summarized outlook.

iWindsurf's site is a front end for WeatherFlow's JSON API. A signed-in page
embeds a session token (`var token = '...'`) that the API accepts as
`wf_token`, so we sign in like a browser, read the token, then call the API.
"""

import logging
import time
from datetime import datetime

import httpx

from ..config import CFG, TZ, env
from . import outlook

log = logging.getLogger(__name__)

API = "https://api.weatherflow.com/wxengine/rest/"
LOGIN = "https://secure.iwindsurf.com/?app=wx&rd=profile"
UNITS = {"units_wind": "kts", "units_temp": "f", "units_distance": "mi",
         "units_precip": "in", "units_height": "ft"}
FORECAST_EVERY = 1800
LIVE_MAX_AGE = 45 * 60  # older station readings are shown as missing

_token: str | None = None
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
    """Call the API, signing in (again) if there is no token or it was rejected."""
    global _token
    for attempt in (1, 2):
        if not _token:
            _token = await _login(c)
        r = await c.get(API + path, params={**UNITS, **params, "wf_token": _token})
        body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/")) else {}
        status = body.get("status", {}).get("status_code", r.status_code)
        if r.status_code == 200 and status == 0:
            return body
        log.warning("%s returned %s %s; re-authenticating", path, r.status_code,
                    body.get("status", {}).get("status_message"))
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


async def fetch() -> dict:
    global _forecast
    cfg = CFG["wind"]
    ids = [s["id"] for s in cfg["spots"]]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 (eink dashboard)"}) as c:
        live = _live(await _api(c, "spot/getSpotDetailSetByList", spot_types="1,100,101",
                                spot_list=",".join(map(str, ids))))
        if not _forecast or time.time() - _forecast[0] > FORECAST_EVERY:
            _forecast = (time.time(), await _api(c, "forecast/getOperationalForecast",
                                                 forecast_id=cfg["forecast_id"]))
    fc = _forecast[1]

    # The daily forecast is for its valid date only; don't show yesterday's.
    valid = fc["daily"].get("valid_time_local", "")[:10]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    spots = [{
        "name": s["name"],
        "now": live.get(s["id"]),
        "forecast": _spot_forecast(fc, s["id"], cfg["hours"]) if valid == today else None,
    } for s in cfg["spots"]]

    return {
        "spots": spots,
        "outlook": await outlook.summarize(fc.get("extended", {}).get("days", [])),
        "issued": fc["daily"].get("issued_timestamp_local", ""),
        "placeholder": False,
    }
