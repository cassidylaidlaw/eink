"""Turn raw source data into the view model the template renders."""

import re
from datetime import datetime, timedelta

from .config import CFG, TZ

CONDITIONS = {
    "sunny": ("Sunny", "sun"),
    "clear-night": ("Clear", "moon"),
    "partlycloudy": ("Partly cloudy", "cloud-sun"),
    "cloudy": ("Cloudy", "cloud"),
    "fog": ("Fog", "cloud-fog"),
    "rainy": ("Rain", "cloud-rain"),
    "pouring": ("Heavy rain", "cloud-storm"),
    "lightning": ("Thunderstorms", "cloud-bolt"),
    "lightning-rainy": ("Thunderstorms", "cloud-bolt"),
    "windy": ("Windy", "wind"),
    "windy-variant": ("Windy", "wind"),
    "snowy": ("Snow", "snowflake"),
    "snowy-rainy": ("Sleet", "cloud-snow"),
    "hail": ("Hail", "cloud-snow"),
    "exceptional": ("Alert", "alert-triangle"),
}

# Compass point the wind blows FROM -> degrees the arrow points (downwind).
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def arrow_deg(direction: str) -> float | None:
    if direction not in COMPASS:
        return None
    return (COMPASS.index(direction) * 22.5 + 180) % 360


def shade(knots_hi: int) -> str:
    if knots_hi >= 22:
        return "k"
    if knots_hi >= 18:
        return "d2"
    if knots_hi >= 14:
        return "d1"
    return ""


def fmt_time(dt: datetime) -> str:
    dt = dt.astimezone(TZ)
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"


def fmt_hour(h: int) -> str:
    return f"{h % 12 or 12}{'a' if h < 12 else 'p'}"


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(TZ)


def _num(states: dict, eid: str | None) -> float | None:
    s = states.get(eid or "")
    try:
        return float(s["state"])
    except (TypeError, KeyError, ValueError):
        return None


def build_weather(ha: dict, now: datetime) -> dict:
    w = ha["states"].get(CFG["weather_entity"], {})
    label, icon = CONDITIONS.get(w.get("state"), ("—", "question-mark"))
    temp = w.get("attributes", {}).get("temperature")

    high = low = None
    for p in ha["twice_daily"]:
        start = _dt(p["datetime"])
        if p.get("is_daytime") and start.date() == now.date() and high is None:
            high = p["temperature"]
        elif not p.get("is_daytime") and low is None and start.date() == now.date():
            low = p["temperature"]

    hourly = []
    for offset in (1, 3, 5, 7):
        target = now + timedelta(hours=offset)
        for p in ha["hourly"]:
            if _dt(p["datetime"]) >= target.replace(minute=0, second=0, microsecond=0):
                hourly.append((fmt_hour(_dt(p["datetime"]).hour), round(p["temperature"])))
                break

    sun = ha["states"].get("sun.sun", {}).get("attributes", {})
    note = f"Sunset {fmt_time(_dt(sun['next_setting']))}" if sun.get("next_setting") else ""

    return {
        "temp": round(temp) if temp is not None else "—",
        "label": label,
        "icon": icon,
        "high": high,
        "low": low,
        "hourly": hourly,
        "note": note,
    }


def build_holiday(ha: dict, now: datetime) -> dict | None:
    jc = CFG["jewish_calendar"]
    st = ha["states"]
    holiday = (st.get(jc["holiday"], {}).get("state") or "").strip()
    if holiday.lower() in ("", "none", "unknown", "unavailable"):
        holiday = ""
    in_effect = st.get(jc["issur_melacha"], {}).get("state") == "on"

    lines = []
    candles = st.get(jc["candle_lighting"], {}).get("state")
    havdalah = st.get(jc["havdalah"], {}).get("state")
    try:
        c = _dt(candles)
        if c > now:
            lines.append(f"Candles {c:%a} {fmt_time(c)}")
    except (TypeError, ValueError):
        c = None
    try:
        h = _dt(havdalah)
        if h > now:
            lines.append(f"Havdalah {h:%a} {fmt_time(h)}")
    except (TypeError, ValueError):
        h = None

    if holiday and now.weekday() == 5:
        title = f"{holiday} · Shabbat"
    elif holiday:
        title = holiday
    elif in_effect or now.weekday() == 5:
        title = "Shabbat"
    elif c and c > now and c - now < timedelta(days=7):
        title = "Shabbat" if c.weekday() == 4 else "Yom Tov"
    else:
        title = ""

    if not lines and not title:
        return None
    date = st.get(jc["date"], {}).get("state", "")
    return {"title": title or "Jewish calendar", "date": date, "lines": lines}


def build_house(ha: dict) -> list[dict]:
    st = ha["states"]
    tiles = []
    for t in CFG["house"]:
        temp = _num(st, t.get("temp"))
        subs = []
        hum = _num(st, t.get("humidity"))
        if hum is not None:
            subs.append(f"{round(hum)}% RH")
        if "extra" in t:
            val = st.get(t["extra"]["entity"], {}).get("state", "?")
            subs.append(f"{t['extra'].get('prefix', '')}{val}")
        if "ok_below" in t and temp is not None:
            subs.append(f"OK (<{t['ok_below']}°)" if temp < t["ok_below"] else "TOO WARM")
        tiles.append({
            "label": t["label"],
            "value": f"{temp:.1f}°" if temp is not None else "—",
            "sub": " · ".join(subs),
        })

    return tiles


PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}


def build_attention(ha: dict, stale: list[str]) -> list[str]:
    """Open attention items, most urgent first. Priority comes from the
    '[attention] priority=... key=...' marker line that script.attention_add writes."""
    items = []
    for it in ha.get("attention", []):
        m = re.search(r"\[attention\] priority=(\w+)", it.get("description") or "")
        prio = m.group(1) if m else "normal"
        items.append((PRIORITY_ORDER.get(prio, 2), it.get("due") or "", it["summary"]))
    items.sort()
    return [s for _, _, s in items] + [f"{name} data stale" for name in stale]


def build(ha: dict | None, wind: dict | None, stale: list[str], now: datetime) -> dict:
    ha = ha or {"states": {}, "hourly": [], "twice_daily": [], "attention": []}
    weather = build_weather(ha, now)
    hours = CFG["wind"]["hours"]
    return {
        "location": CFG["location_label"],
        "date_label": f"{now:%a %b} {now.day}",
        "weather": weather,
        "holiday": build_holiday(ha, now),
        "house": build_house(ha),
        "house_slots": CFG.get("house_slots", 4),
        "attention": build_attention(ha, stale),
        "wind": wind,
        "wind_hours": [fmt_hour(h) if i in (0, len(hours) - 1) else str(h % 12 or 12)
                       for i, h in enumerate(hours)],
        "footer_name": CFG["footer_name"],
        "updated": fmt_time(now),
    }
