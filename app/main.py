import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import byos, data, render
from .config import CFG, DATA_DIR, TZ, VENDOR_DIR
from .sources import ha, wind

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("eink")

# A source counts as stale once its last success is this old.
STALE_AFTER = 3 * CFG["render_every"]


class Source:
    """Keeps the last good result of a fetcher so one failure never blanks the screen."""

    def __init__(self, name, fetch):
        self.name, self.fetch = name, fetch
        self.value, self.ok_at = None, 0.0

    async def refresh(self):
        try:
            self.value = await self.fetch()
            self.ok_at = time.time()
        except Exception as e:
            log.warning("%s fetch failed: %s", self.name, e)

    @property
    def stale(self) -> bool:
        return time.time() - self.ok_at > STALE_AFTER


class Screen:
    digest = "none"
    view: dict = {}


SOURCES = [
    Source("Home Assistant", lambda: ha.fetch(CFG["weather_entity"])),
    Source("Wind", wind.fetch),
]


async def update(app: FastAPI):
    await asyncio.gather(*(s.refresh() for s in SOURCES))
    ha_src, wind_src = SOURCES
    stale = [s.name for s in SOURCES if s.stale]
    app.state.screen.view = data.build(ha_src.value, wind_src.value, stale, datetime.now(TZ))
    png = await app.state.renderer.screenshot()
    bw, digest = render.to_one_bit(png)
    render.save(bw)
    if digest != app.state.screen.digest:
        log.info("new screen %s", digest)
    app.state.screen.digest = digest


async def loop(app: FastAPI):
    await asyncio.sleep(1)  # let uvicorn start serving /dashboard
    while True:
        try:
            await update(app)
        except Exception:
            log.exception("render failed; keeping previous screen")
        await asyncio.sleep(CFG["render_every"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.screen = Screen()
    app.state.renderer = render.Renderer()
    await app.state.renderer.start()
    task = asyncio.create_task(loop(app))
    yield
    task.cancel()
    await app.state.renderer.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(byos.router)
if VENDOR_DIR.exists():
    app.mount("/vendor", StaticFiles(directory=VENDOR_DIR), name="vendor")


@app.get("/")
async def root():
    return RedirectResponse("/preview")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return render.render_html(app.state.screen.view)


@app.get("/screen.bmp")
async def screen_bmp():
    return FileResponse(DATA_DIR / "screen.bmp", media_type="image/bmp")


@app.get("/screen.png")
async def screen_png():
    return FileResponse(DATA_DIR / "screen.png", media_type="image/png")


@app.post("/render")
async def force_render():
    await update(app)
    return {"digest": app.state.screen.digest}


@app.get("/preview", response_class=HTMLResponse)
async def preview():
    s = app.state.screen
    return f"""<!doctype html><meta http-equiv="refresh" content="60">
<body style="background:#333;margin:0;padding:24px;font-family:sans-serif;color:#ddd">
<p>1-bit render as sent to the display · {s.digest} ·
<a style="color:#9cf" href="/dashboard">live HTML</a> ·
<form style="display:inline" method="post" action="/render"><button>Re-render</button></form></p>
<img src="/screen.png?v={s.digest}" width="800" height="480" style="image-rendering:pixelated;background:#fff">
</body>"""
