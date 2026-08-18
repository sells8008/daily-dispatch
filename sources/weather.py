"""Weather source — Open-Meteo, no API key required."""

import logging

import requests

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> (text, glyph)
WEATHER_CODES = {
    0: ("Clear", "☀︎"),
    1: ("Mainly clear", "🌤"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫"),
    48: ("Fog", "🌫"),
    51: ("Light drizzle", "🌦"),
    53: ("Drizzle", "🌦"),
    55: ("Heavy drizzle", "🌦"),
    56: ("Freezing drizzle", "🌧"),
    57: ("Freezing drizzle", "🌧"),
    61: ("Light rain", "🌧"),
    63: ("Rain", "🌧"),
    65: ("Heavy rain", "🌧"),
    66: ("Freezing rain", "🌧"),
    67: ("Freezing rain", "🌧"),
    71: ("Light snow", "🌨"),
    73: ("Snow", "🌨"),
    75: ("Heavy snow", "🌨"),
    77: ("Snow grains", "🌨"),
    80: ("Rain showers", "🌦"),
    81: ("Rain showers", "🌦"),
    82: ("Violent rain showers", "🌦"),
    85: ("Snow showers", "🌨"),
    86: ("Snow showers", "🌨"),
    95: ("Thunderstorm", "⛈"),
    96: ("Thunderstorm w/ hail", "⛈"),
    99: ("Thunderstorm w/ hail", "⛈"),
}


def _describe(code):
    return WEATHER_CODES.get(code, ("Unknown", "—"))


def fetch(location, days=4, timeout=10):
    """Fetch current conditions + a daily forecast for the configured location.

    Returns a dict:
      {
        "current": {"temp": int, "desc": str, "glyph": str},
        "today": {"hi": int, "lo": int},
        "daily": [{"label": "Tue", "hi": int, "lo": int, "desc": str, "glyph": str}, ...],
      }
    Returns None on failure (caller should drop the section gracefully).
    """
    try:
        resp = requests.get(
            FORECAST_URL,
            params={
                "latitude": location["lat"],
                "longitude": location["lon"],
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "current": "temperature_2m,weather_code",
                "temperature_unit": "fahrenheit",
                "timezone": location.get("timezone", "auto"),
                "forecast_days": days,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        current = data["current"]
        cur_desc, cur_glyph = _describe(current["weather_code"])

        daily = data["daily"]
        dates = daily["time"]
        his = daily["temperature_2m_max"]
        los = daily["temperature_2m_min"]
        codes = daily["weather_code"]

        import datetime as _dt

        daily_out = []
        for i, date_str in enumerate(dates):
            d = _dt.date.fromisoformat(date_str)
            desc, glyph = _describe(codes[i])
            daily_out.append(
                {
                    "date": date_str,
                    "label": "Today" if i == 0 else d.strftime("%a"),
                    "hi": round(his[i]),
                    "lo": round(los[i]),
                    "desc": desc,
                    "glyph": glyph,
                }
            )

        return {
            "current": {
                "temp": round(current["temperature_2m"]),
                "desc": cur_desc,
                "glyph": cur_glyph,
            },
            "today": {
                "hi": daily_out[0]["hi"] if daily_out else None,
                "lo": daily_out[0]["lo"] if daily_out else None,
            },
            "daily": daily_out,
        }
    except Exception:
        logger.exception("weather fetch failed")
        return None
