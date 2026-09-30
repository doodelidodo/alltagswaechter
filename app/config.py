"""Load and validate the TOML configuration.

Everything is optional except the ntfy target: a section that is missing simply
switches that check off. Environment variables override the secrets so the
config file itself can live in a repository.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS = DAYS[:5]
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    pass


@dataclass
class Ntfy:
    url: str
    topic: str
    token: str = ""
    username: str = ""
    password: str = ""


@dataclass
class Route:
    name: str
    origin: str
    destination: str
    departures: list[str]
    days: list[str] = field(default_factory=lambda: list(WEEKDAYS))
    watch_minutes: int = 60
    poll_minutes: int = 3
    min_delay: int = 3


@dataclass
class Weather:
    name: str
    latitude: float
    longitude: float
    at: str = "06:45"
    days: list[str] = field(default_factory=lambda: list(DAYS))
    window: tuple[str, str] = ("07:00", "19:00")
    rain_mm: float = 0.3
    gust_kmh: float = 60.0
    frost_c: float = 1.0
    frost_window: tuple[str, str] = ("06:00", "09:00")
    model: str = "meteoswiss_icon_ch2"
    always: bool = False


@dataclass
class Waste:
    at: str = "19:00"
    openerz_zip: int | None = None
    ical_url: str = ""
    types: list[str] = field(default_factory=list)
    schedule: list["WasteRule"] = field(default_factory=list)


@dataclass
class WasteRule:
    """A collection typed in by hand – for municipalities that only publish a PDF."""
    name: str
    dates: list[str] = field(default_factory=list)      # "2026-10-14"
    weekdays: list[str] = field(default_factory=list)   # every "mon" …
    skip: list[str] = field(default_factory=list)       # … except these dates
    start: str = ""                                      # rule valid from (optional)
    end: str = ""                                        # … until (optional)
    note: str = ""                                       # added to the push


@dataclass
class Config:
    ntfy: Ntfy
    language: str = "de"
    timezone: str = "Europe/Zurich"
    state_file: str = "/data/state.json"
    routes: list[Route] = field(default_factory=list)
    weather: Weather | None = None
    waste: Waste | None = None


def _time(value, where: str) -> str:
    if not isinstance(value, str) or not _HHMM.match(value):
        raise ConfigError(f"{where}: expected a time like \"07:12\", got {value!r}")
    return value


def _date(value, where: str) -> str:
    # TOML has native dates; accept those and "YYYY-MM-DD" strings
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        raise ConfigError(f"{where}: expected a date like 2026-10-14, got {value!r}")
    return text


def _days(value, where: str) -> list[str]:
    if value is None:
        return None
    if value == "weekdays":
        return list(WEEKDAYS)
    if value == "daily":
        return list(DAYS)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: expected a list like [\"mon\", \"tue\"], \"weekdays\" or \"daily\"")
    out = []
    for d in value:
        d = str(d).lower()[:3]
        if d not in DAYS:
            raise ConfigError(f"{where}: unknown day {d!r} (use {', '.join(DAYS)})")
        out.append(d)
    return out


def _window(value, where: str) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) != 2:
        raise ConfigError(f"{where}: expected [\"HH:MM\", \"HH:MM\"]")
    start, end = _time(value[0], where), _time(value[1], where)
    if start >= end:
        raise ConfigError(f"{where}: start must be before end")
    return (start, end)


def parse(data: dict) -> Config:
    n = data.get("ntfy") or {}
    ntfy = Ntfy(
        url=(os.environ.get("NTFY_URL") or n.get("url") or "").rstrip("/"),
        topic=os.environ.get("NTFY_TOPIC") or n.get("topic") or "",
        token=os.environ.get("NTFY_TOKEN") or n.get("token") or "",
        username=os.environ.get("NTFY_USERNAME") or n.get("username") or "",
        password=os.environ.get("NTFY_PASSWORD") or n.get("password") or "",
    )
    if not ntfy.url or not ntfy.topic:
        raise ConfigError("[ntfy]: url and topic are required (or NTFY_URL / NTFY_TOPIC)")

    language = data.get("language", "de")
    if language not in ("de", "en"):
        raise ConfigError("language: \"de\" or \"en\"")

    cfg = Config(
        ntfy=ntfy,
        language=language,
        timezone=data.get("timezone", "Europe/Zurich"),
        state_file=os.environ.get("STATE_FILE") or data.get("state_file", "/data/state.json"),
    )

    for i, r in enumerate(data.get("transit", [])):
        where = f"[[transit]] #{i + 1}"
        for key in ("from", "to", "departures"):
            if key not in r:
                raise ConfigError(f"{where}: \"{key}\" is required")
        deps = [_time(t, f"{where} departures") for t in r["departures"]]
        route = Route(
            name=r.get("name") or f"{r['from']} → {r['to']}",
            origin=r["from"],
            destination=r["to"],
            departures=deps,
        )
        route.days = _days(r.get("days"), f"{where} days") or route.days
        route.watch_minutes = int(r.get("watch_minutes", route.watch_minutes))
        route.poll_minutes = max(1, int(r.get("poll_minutes", route.poll_minutes)))
        route.min_delay = max(1, int(r.get("min_delay", route.min_delay)))
        cfg.routes.append(route)

    if "weather" in data:
        w = data["weather"]
        where = "[weather]"
        if "latitude" not in w or "longitude" not in w:
            raise ConfigError(f"{where}: latitude and longitude are required")
        weather = Weather(
            name=w.get("name", ""),
            latitude=float(w["latitude"]),
            longitude=float(w["longitude"]),
        )
        weather.at = _time(w.get("at", weather.at), f"{where} at")
        weather.days = _days(w.get("days"), f"{where} days") or weather.days
        if "window" in w:
            weather.window = _window(w["window"], f"{where} window")
        if "frost_window" in w:
            weather.frost_window = _window(w["frost_window"], f"{where} frost_window")
        weather.rain_mm = float(w.get("rain_mm", weather.rain_mm))
        weather.gust_kmh = float(w.get("gust_kmh", weather.gust_kmh))
        weather.frost_c = float(w.get("frost_c", weather.frost_c))
        weather.model = w.get("model", weather.model)
        weather.always = bool(w.get("always", weather.always))
        cfg.weather = weather

    if "waste" in data:
        w = data["waste"]
        where = "[waste]"
        waste = Waste(
            at=_time(w.get("at", "19:00"), f"{where} at"),
            openerz_zip=int(w["openerz_zip"]) if w.get("openerz_zip") else None,
            ical_url=w.get("ical_url", ""),
            types=[str(t) for t in w.get("types", [])],
        )
        for j, r in enumerate(w.get("schedule", [])):
            rw = f"[[waste.schedule]] #{j + 1}"
            if not r.get("name"):
                raise ConfigError(f"{rw}: \"name\" is required")
            rule = WasteRule(
                name=r["name"],
                dates=[_date(d, rw) for d in r.get("dates", [])],
                weekdays=_days(r.get("weekdays"), f"{rw} weekdays") or [],
                skip=[_date(d, rw) for d in r.get("skip", [])],
                start=_date(r["start"], rw) if r.get("start") else "",
                end=_date(r["end"], rw) if r.get("end") else "",
                note=str(r.get("note", "")),
            )
            if not rule.dates and not rule.weekdays:
                raise ConfigError(f"{rw}: give dates or weekdays")
            waste.schedule.append(rule)
        if not waste.openerz_zip and not waste.ical_url and not waste.schedule:
            raise ConfigError(f"{where}: set openerz_zip (City of Zurich), ical_url or [[waste.schedule]]")
        cfg.waste = waste

    return cfg


def load(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    return parse(data)
