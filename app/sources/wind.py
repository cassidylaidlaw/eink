"""Wind forecast, live readings, and outlook.

Placeholder data until the iWindsurf fetcher is written; the shape here is
the contract the template renders.
"""


async def fetch() -> dict:
    return {
        "spots": [
            {
                "name": "Berkeley",
                "now": {"avg": 12, "gust": 16, "dir": "WSW"},
                "forecast": [(13, 17, "WSW"), (9, 13, "WSW"), (9, 13, "WSW"),
                             (13, 17, "WSW"), (13, 17, "WSW"), (9, 13, "NNW")],
            },
            {
                "name": "Albany",
                "now": {"avg": 9, "gust": 12, "dir": "SW"},
                "forecast": [(10, 14, "SW"), (6, 10, "SSW"), (6, 10, "SSW"),
                             (13, 17, "WNW"), (13, 17, "WNW"), (7, 11, "WNW")],
            },
            {
                "name": "Treasure Is.",
                "now": {"avg": 18, "gust": 23, "dir": "WSW"},
                "forecast": [(19, 23, "WSW"), (16, 20, "WSW"), (16, 20, "WSW"),
                             (20, 24, "WSW"), (20, 24, "WSW"), (13, 17, "WSW")],
            },
            {
                "name": "Candlestick",
                "now": {"avg": 15, "gust": 19, "dir": "W"},
                "forecast": [(15, 19, "W"), (16, 20, "W"), (16, 20, "W"),
                             (17, 21, "W"), (17, 21, "W"), (14, 18, "W")],
            },
        ],
        "outlook": [
            {"day": "Sat", "text": "Eddy, weak gradient. 15–19 Gate→Slot only; light elsewhere."},
            {"day": "Sun", "text": "NW returns. 15–19 Gate→Treasure Is. in the PM."},
            {"day": "Mon", "text": "AM southerlies, then 15–19 Gate→Treasure Is. PM."},
        ],
        "placeholder": True,
    }
