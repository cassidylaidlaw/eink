"""HTML -> 800x480 screenshot -> 1-bit BMP/PNG for the display."""

import hashlib
import io
import logging

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from playwright.async_api import Browser, async_playwright

from .config import DATA_DIR, PORT
from .data import arrow_deg, shade

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 800, 480
# Grey levels above this become white. Dither patterns are drawn in pure
# black, so this mostly decides how anti-aliased text edges fall.
THRESHOLD = 150

jinja = Environment(
    loader=FileSystemLoader(__file__.rsplit("/", 1)[0] + "/templates"),
    autoescape=select_autoescape(),
)
jinja.globals.update(arrow_deg=arrow_deg, shade=shade)


def render_html(view: dict) -> str:
    return jinja.get_template("dashboard.html").render(**view)


class Renderer:
    def __init__(self):
        self._pw = None
        self._browser: Browser | None = None

    async def start(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch()

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def screenshot(self) -> bytes:
        """Screenshot the live /dashboard route (served by this same app)."""
        page = await self._browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        try:
            await page.goto(f"http://127.0.0.1:{PORT}/dashboard", wait_until="networkidle")
            await page.evaluate("document.fonts.ready")
            return await page.screenshot(type="png")
        finally:
            await page.close()


def to_one_bit(png: bytes) -> tuple[Image.Image, str]:
    grey = Image.open(io.BytesIO(png)).convert("L")
    bw = grey.point(lambda p: 255 if p > THRESHOLD else 0).convert("1", dither=Image.Dither.NONE)
    digest = hashlib.sha1(bw.tobytes()).hexdigest()[:12]
    return bw, digest


def save(bw: Image.Image) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, fmt in (("screen.bmp", "BMP"), ("screen.png", "PNG")):
        tmp = DATA_DIR / f".{name}"
        bw.save(tmp, fmt)
        tmp.replace(DATA_DIR / name)
