"""Morning weather briefing – only when it matters.

Source: Open-Meteo (free, no key), by default with MeteoSwiss' own ICON-CH
model. Falls back to Open-Meteo's automatic model choice if the requested
model is unavailable.

Sends a push only for rain in your window, strong gusts or frost in the
morning – unless `always = true`.
"""
from __future__ import annotations

import logging
from datetime import datetime

from ..config import Weather
from ..http import FetchError, get_json
from ..i18n import t
from ..notify import Message, Notifier

log = logging.getLogger(__name__)
API = "https://api.open-meteo.com/v1/forecast"


def fetch(w: Weather, tz_name: str) -> dict:
    params = {
        "latitude": w.latitude,
        "longitude": w.longitude,
        "hourly": "precipitation,temperature_2m,wind_gusts_10m",
        "forecast_days": 1,
        "timezone": tz_name,
    }
    if w.model:
        try:
            return get_json(API, {**params, "models": w.model})
        except FetchError as exc:
            log.warning("weather model %s unavailable, using default: %s", w.model, exc)
    return get_json(API, params)


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def assess(w: Weather, data: dict, lang: str) -> Message | None:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    rain = hourly.get("precipitation") or []
    temp = hourly.get("temperature_2m") or []
    gust = hourly.get("wind_gusts_10m") or []

    def rows(window):
        start, end = window
        for i, ts in enumerate(times):
            hhmm = ts[11:16]
            if start <= hhmm < end:
                yield i, hhmm

    day = list(rows(w.window))
    if not day:
        return None
    where = f" in {w.name}" if w.name else ""  # same preposition in de and en

    rain_hours = [(h, rain[i]) for i, h in day if i < len(rain) and rain[i] is not None and rain[i] >= w.rain_mm]
    gusts = [gust[i] for i, _ in day if i < len(gust) and gust[i] is not None]
    temps = [temp[i] for i, _ in day if i < len(temp) and temp[i] is not None]
    frost = [temp[i] for i, _ in rows(w.frost_window) if i < len(temp) and temp[i] is not None]

    lines, tags, title = [], [], None
    if rain_hours:
        start = rain_hours[0][0]
        last = rain_hours[-1][0]
        end = f"{int(last[:2]) + 1:02d}:00"
        mm = max(v for _, v in rain_hours)
        title = t(lang, "rain_title", start=start[:2].lstrip("0") or "0", where=where)
        lines.append(t(lang, "rain_body", start=start, end=end, mm=_num(round(mm, 1))))
        tags.append("umbrella")
    max_gust = max(gusts) if gusts else 0
    if max_gust >= w.gust_kmh:
        lines.append(t(lang, "gust_body", kmh=round(max_gust)))
        tags.append("dash")
    min_frost = min(frost) if frost else 99
    if min_frost <= w.frost_c:
        lines.append(t(lang, "frost_body", temp=_num(round(min_frost, 1))))
        tags.append("snowflake")

    tmin, tmax = (round(min(temps)), round(max(temps))) if temps else ("?", "?")
    if not lines:
        if not w.always:
            return None
        return Message(t(lang, "weather_title_other", where=where),
                       t(lang, "weather_ok", tmin=tmin, tmax=tmax), tags=["sunny"], priority=2)
    lines.append(t(lang, "weather_range", tmin=tmin, tmax=tmax))
    return Message(title or t(lang, "weather_title_other", where=where), "\n".join(lines), tags=tags, priority=3)


class WeatherCheck:
    def __init__(self, w: Weather, tz_name: str, lang: str, fetcher=fetch):
        self.w, self.tz_name, self.lang, self.fetcher = w, tz_name, lang, fetcher

    def run(self, now: datetime, notifier: Notifier) -> bool:
        """Returns True when done for today (sent, or nothing to report)."""
        try:
            data = self.fetcher(self.w, self.tz_name)
        except FetchError as exc:
            log.warning("weather: %s", exc)
            return False
        msg = assess(self.w, data, self.lang)
        if msg is None:
            log.info("weather: nothing to report")
            return True
        return notifier.send(msg)
