"""Home Assistant REST client: entity states and weather forecasts."""

import httpx

from ..config import env


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=env("HA_URL").rstrip("/"),
        headers={"Authorization": f"Bearer {env('HA_TOKEN')}"},
        timeout=15,
    )


async def fetch(weather_entity: str, attention_list: str) -> dict:
    """Return {"states": {...}, "hourly": [...], "twice_daily": [...], "attention": [todo items]}."""
    async with _client() as c:
        r = await c.get("/api/states")
        r.raise_for_status()
        states = {s["entity_id"]: s for s in r.json()}

        forecasts = {}
        for kind in ("hourly", "twice_daily"):
            r = await c.post(
                "/api/services/weather/get_forecasts?return_response",
                json={"entity_id": weather_entity, "type": kind},
            )
            r.raise_for_status()
            forecasts[kind] = r.json()["service_response"][weather_entity]["forecast"]

        r = await c.post(
            "/api/services/todo/get_items?return_response",
            json={"entity_id": attention_list, "status": "needs_action"},
        )
        r.raise_for_status()
        attention = r.json()["service_response"][attention_list]["items"]

    return {"states": states, **forecasts, "attention": attention}
