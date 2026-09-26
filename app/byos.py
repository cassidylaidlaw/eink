"""TRMNL "bring your own server" API that the device firmware talks to.

Contract: https://github.com/usetrmnl/trmnl-firmware (README, API section).
"""

import json
import logging
import secrets
from datetime import datetime, timedelta

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


def _image_url(state) -> str:
    return f"{env('PUBLIC_URL').rstrip('/')}/{DEVICE_IMAGE}?v={state.digest}"


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
    return {"status": 200, "api_key": dev["api_key"], "friendly_id": dev["friendly_id"],
            "image_url": _image_url(state), "filename": state.digest}


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

    state = request.app.state.screen
    return {
        "status": 0,
        "image_url": _image_url(state),
        "filename": state.digest,
        "update_firmware": False,
        "firmware_url": None,
        "refresh_rate": str(refresh_seconds(now)),
        "reset_firmware": False,
    }


@router.post("/log")
async def device_log(request: Request):
    log.warning("device log: %s", (await request.body()).decode(errors="replace")[:4000])
    return {"status": 200}
