"""Waste collection reminder the evening before.

Sources:
- OpenERZ (openerz.metaodi.ch) – City of Zurich, by postcode.
- Any iCal/ICS feed – many Swiss municipalities and waste apps offer one.
- A schedule typed into the config – for municipalities that only publish a PDF.

`types` filters what you care about. For OpenERZ use its keys (paper,
cardboard, organic, …); for iCal any word that appears in the event title
("Karton", "Grüngut"). Empty means everything.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from ..config import DAYS, Waste, WasteRule
from ..http import FetchError, get, get_json
from ..i18n import join, t, waste_name
from ..notify import Message, Notifier

log = logging.getLogger(__name__)
OPENERZ = "https://openerz.metaodi.ch/api/calendar"


def fetch_openerz(zip_code: int, day: date) -> list[dict]:
    data = get_json(OPENERZ, {"zip": zip_code, "start": day.isoformat(), "end": day.isoformat(), "limit": 50})
    out = []
    for row in data.get("result") or []:
        if row.get("date") == day.isoformat():
            out.append({"key": row.get("waste_type", ""), "station": row.get("station") or ""})
    return out


def parse_ical(text: str) -> list[tuple[date, str]]:
    """Minimal VEVENT reader: DTSTART + SUMMARY. Handles folded lines and escapes."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    events, current = [], None
    for line in unfolded.splitlines():
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current and "start" in current:
                events.append((current["start"], current.get("summary", "")))
            current = None
        elif current is not None and ":" in line:
            name, value = line.split(":", 1)
            prop = name.split(";", 1)[0].upper()
            if prop == "DTSTART":
                m = re.match(r"(\d{4})(\d{2})(\d{2})", value)
                if m:
                    current["start"] = date(int(m[1]), int(m[2]), int(m[3]))
            elif prop == "SUMMARY":
                current["summary"] = (value.replace("\\,", ",").replace("\\;", ";")
                                      .replace("\\n", " ").replace("\\\\", "\\").strip())
    return events


def fetch_ical(url: str, day: date) -> list[dict]:
    text = get(url).decode("utf-8", "replace")
    return [{"key": summary, "station": ""} for d, summary in parse_ical(text) if d == day]


def from_schedule(rules: list[WasteRule], day: date) -> list[dict]:
    iso, weekday = day.isoformat(), DAYS[day.weekday()]
    out = []
    for r in rules:
        if iso in r.skip:
            continue
        in_range = (not r.start or iso >= r.start) and (not r.end or iso <= r.end)
        if iso in r.dates or (weekday in r.weekdays and in_range):
            out.append({"key": r.name, "station": "", "note": r.note})
    return out


def select(items: list[dict], types: list[str], lang: str) -> list[dict]:
    if not types:
        return items
    wanted = [x.lower() for x in types]
    out = []
    for it in items:
        key = it["key"].lower()
        label = waste_name(lang, it["key"]).lower()
        if any(w == key or w in key or w in label for w in wanted):
            out.append(it)
    return out


def build_message(items: list[dict], day: date, lang: str) -> Message | None:
    if not items:
        return None
    names = []
    for it in items:
        n = waste_name(lang, it["key"])
        if n not in names:
            names.append(n)
    body = [t(lang, "waste_body", date=day.strftime("%d.%m."))]
    for it in items:
        if it["station"]:
            body.append(t(lang, "waste_body_station", type=waste_name(lang, it["key"]), station=it["station"]))
        if it.get("note"):
            body.append(f"{waste_name(lang, it['key'])}: {it['note']}")
    return Message(t(lang, "waste_title", types=join(lang, names)), "\n".join(body),
                   tags=["wastebasket"], priority=3)


class WasteCheck:
    def __init__(self, w: Waste, lang: str, openerz=fetch_openerz, ical=fetch_ical):
        self.w, self.lang, self.openerz, self.ical = w, lang, openerz, ical

    def run(self, now: datetime, notifier: Notifier) -> bool:
        tomorrow = (now + timedelta(days=1)).date()
        items: list[dict] = []
        try:
            if self.w.openerz_zip:
                items += self.openerz(self.w.openerz_zip, tomorrow)
            if self.w.ical_url:
                items += self.ical(self.w.ical_url, tomorrow)

        except FetchError as exc:
            log.warning("waste: %s", exc)
            return False
        # hand-typed rules are chosen deliberately, so the type filter does not apply
        chosen = select(items, self.w.types, self.lang) + from_schedule(self.w.schedule, tomorrow)
        msg = build_message(chosen, tomorrow, self.lang)
        if msg is None:
            log.info("waste: nothing tomorrow")
            return True
        return notifier.send(msg)
