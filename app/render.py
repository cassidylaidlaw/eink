"""HTML -> 800x480 screenshot -> display image (1-bit BMP or 2-bit grayscale PNG)."""

import hashlib
import io
import logging
import os
import struct
import zlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from playwright.async_api import Browser, async_playwright

from .config import CFG, DATA_DIR, PORT
from .data import arrow_deg, shade

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
WIDTH, HEIGHT = 800, 480
BITS = int(CFG.get("bit_depth", 1))
# 1-bit: grey levels above this become white.
THRESHOLD = 150

jinja = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
jinja.globals.update(arrow_deg=arrow_deg, shade=shade)


def render_html(view: dict, bits: int = BITS) -> str:
    return jinja.get_template("dashboard.html").render(**view, bits=bits)


class Renderer:
    def __init__(self, bits: int):
        self.bits = bits
        self._pw = None
        self._browser: Browser | None = None

    async def start(self):
        self._pw = await async_playwright().start()
        env = dict(os.environ)
        if self.bits == 1:
            env["FONTCONFIG_FILE"] = str(HERE / "fonts-mono.conf")
        self._browser = await self._pw.chromium.launch(
            env=env, args=["--font-render-hinting=full", "--disable-font-subpixel-positioning"])

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def screenshot(self) -> bytes:
        """Screenshot the live /dashboard route (served by this same app)."""
        page = await self._browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        try:
            await page.goto(f"http://127.0.0.1:{PORT}/dashboard?bits={self.bits}", wait_until="networkidle")
            await page.evaluate("document.fonts.ready")
            return await page.screenshot(type="png")
        finally:
            await page.close()


def png_gray2(levels: Image.Image) -> bytes:
    """Encode an image of values 0-3 as a 2-bit grayscale PNG (color type 0).

    Pillow can only write 2-bit PNGs as palette images; TRMNL's documented
    format is true grayscale, so pack it by hand.
    """
    w, h = levels.size
    px = levels.tobytes()
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: none
        row = px[y * w:(y + 1) * w]
        for x in range(0, w, 4):
            q = row[x:x + 4].ljust(4, b"\0")
            raw.append(q[0] << 6 | q[1] << 4 | q[2] << 2 | q[3])

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 2, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def convert(png: bytes, bits: int = BITS) -> tuple[dict[str, bytes], str]:
    """Return ({filename: bytes}, digest) at the given bit depth."""
    grey = Image.open(io.BytesIO(png)).convert("L")
    if bits == 2:
        levels = grey.point(lambda p: min(3, (p + 42) // 85))
        digest = hashlib.sha1(levels.tobytes()).hexdigest()[:12]
        return {"screen.png": png_gray2(levels)}, digest

    bw = grey.point(lambda p: 255 if p > THRESHOLD else 0).convert("1", dither=Image.Dither.NONE)
    digest = hashlib.sha1(bw.tobytes()).hexdigest()[:12]
    out = {}
    for name, fmt in (("screen.bmp", "BMP"), ("screen.png", "PNG")):
        buf = io.BytesIO()
        bw.save(buf, fmt)
        out[name] = buf.getvalue()
    return out, digest


def save(files: dict[str, bytes]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        tmp = DATA_DIR / f".{name}"
        tmp.write_bytes(body)
        tmp.replace(DATA_DIR / name)


# Image the device downloads: BMP is the most widely supported 1-bit format.
DEVICE_IMAGE = "screen.png" if BITS == 2 else "screen.bmp"
