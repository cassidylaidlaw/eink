"""TRMNL "bring your own server" API that the device firmware talks to.

Contract: https://github.com/usetrmnl/trmnl-firmware (README, API section).
"""

import asyncio
import json
import logging
import secrets
import time
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from .config import CFG, DATA_DIR, TZ, env
from .render import DEVICE_IMAGE

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

DEVICES = DATA_DIR / "devices.json"


def load_devices() -> dict:
    try:
        return json.loads(DEVICES.read_text())
    except FileNotFoundError:
        return {}


def save_devices(devices: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES.with_suffix(".tmp")
    tmp.write_text(json.dumps(devices, indent=2))
    tmp.replace(DEVICES)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def refresh_seconds(now: datetime) -> int:
    """Seconds until the device should wake next, from config.refresh."""
    cur = now.hour * 60 + now.minute
    for w in CFG["refresh"]:
        start, end = _minutes(w["from"]), _minutes(w["to"])
        inside = start <= cur < end if start <= end else (cur >= start or cur < end)
        if not inside:
            continue
        if w.get("sleep"):
            wake = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
            if wake <= now:
                wake += timedelta(days=1)
            return max(300, int((wake - now).total_seconds()))
        return int(w["seconds"])
    return 1800


def _filename(state) -> str:
    """Unique per check-in, even when the image hasn't changed.

    Works around trmnl-firmware#477: when a wake sees the same filename as the
    last one, the firmware takes its "old image, don't redraw" path and then
    hangs entering deep sleep, so the RTC watchdog resets it every ~30-44 s
    (seen on the reTerminal E1001 with FW1.8.10). Always sending a new name
    costs a redraw per wake. Revert to plain `state.digest` once fixed upstream.
    """
    return f"{state.digest}-{int(time.time())}"


def _image_url(state, filename: str) -> str:
    return f"{env('PUBLIC_URL').rstrip('/')}/{DEVICE_IMAGE}?v={filename}"


async def _report_checkin(battery_voltage: str) -> None:
    """Tell HA the display checked in (automation "E-ink display - check-in"),
    so its attention sync can flag a display that went quiet or runs low."""
    hook = env("EINK_WEBHOOK_ID")
    if not hook:
        return
    try:
        volts = float(battery_voltage)
    except ValueError:
        volts = None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{env('HA_URL').rstrip('/')}/api/webhook/{hook}",
                             json={"timestamp": int(time.time()), "battery_voltage": volts})
            r.raise_for_status()
    except Exception as e:
        log.warning("check-in webhook to HA failed: %s", e)


_background: set[asyncio.Task] = set()


@router.get("/setup")
async def setup(request: Request, id: str = Header(default="", alias="ID")):
    mac = id.upper()
    allowed = [m.strip().upper() for m in env("ALLOWED_MACS").split(",") if m.strip()]
    if not mac or (allowed and mac not in allowed):
        log.warning("setup refused for %r", mac)
        return JSONResponse({"status": 404, "api_key": None, "friendly_id": None,
                             "image_url": None, "filename": None}, status_code=404)
    devices = load_devices()
    dev = devices.setdefault(mac, {
        "api_key": secrets.token_urlsafe(16),
        "friendly_id": secrets.token_hex(3).upper(),
    })
    save_devices(devices)
    log.info("device %s registered (%s)", mac, dev["friendly_id"])
    state = request.app.state.screen
    filename = _filename(state)
    return {"status": 200, "api_key": dev["api_key"], "friendly_id": dev["friendly_id"],
            "image_url": _image_url(state, filename), "filename": filename}


@router.get("/display")
async def display(
    request: Request,
    id: str = Header(default="", alias="ID"),
    access_token: str = Header(default="", alias="Access-Token"),
    battery_voltage: str = Header(default="", alias="Battery-Voltage"),
    fw_version: str = Header(default="", alias="FW-Version"),
    rssi: str = Header(default="", alias="RSSI"),
):
    devices = load_devices()
    dev = devices.get(id.upper())
    if not dev or not secrets.compare_digest(dev["api_key"], access_token):
        return JSONResponse({"status": 500, "error": "Device not found"}, status_code=500)

    now = datetime.now(TZ)
    dev.update(last_seen=now.isoformat(), battery_voltage=battery_voltage,
               fw_version=fw_version, rssi=rssi)
    save_devices(devices)
    task = asyncio.create_task(_report_checkin(battery_voltage))
    _background.add(task)
    task.add_done_callback(_background.discard)

    state = request.app.state.screen
    filename = _filename(state)
    return {
        "status": 0,
        "image_url": _image_url(state, filename),
        "filename": filename,
        "update_firmware": False,
        "firmware_url": None,
        "refresh_rate": str(refresh_seconds(now)),
        "reset_firmware": False,
    }


@router.post("/log")
async def device_log(request: Request):
    log.warning("device log: %s", (await request.body()).decode(errors="replace")[:4000])
    return {"status": 200}
