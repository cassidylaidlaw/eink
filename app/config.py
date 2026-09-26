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


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
