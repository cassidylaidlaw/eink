import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
VENDOR_DIR = Path(os.environ.get("VENDOR_DIR", "/srv/vendor"))

with open(os.environ.get("CONFIG_PATH", ROOT / "config.yaml")) as f:
    CFG = yaml.safe_load(f)

TZ = ZoneInfo(CFG["timezone"])
PORT = int(os.environ.get("PORT", "8080"))


def is_evening(now) -> bool:
    """After config.evening_switch, the board shows tomorrow (weather and model wind)."""
    h, m = map(int, CFG.get("evening_switch", "18:00").split(":"))
    return (now.hour, now.minute) >= (h, m)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
