"""Public transport: watch the connections you actually take.

Source: transport.opendata.ch (Swiss public transport API, no key needed).

For each configured departure the check starts polling `watch_minutes` before
departure and reports only *changes*: delay appears, delay grows or shrinks by
3+ minutes, delay recovered, platform changed, connection vanished.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..config import DAYS, Route
from ..http import FetchError, get_json
from ..i18n import t
from ..notify import Message, Notifier
from ..state import State

log = logging.getLogger(__name__)
API = "https://transport.opendata.ch/v1/connections"
REPORT_STEP = 3        # re-notify when the delay moved by this many minutes
MISSING_ROUNDS = 2     # a connection must be absent this often before we say so


def _parse_ts(value: str | None, tz) -> datetime | None:
    if not value:
        return None
    # "2026-09-30T17:02:00+0200" – fromisoformat wants "+02:00" before 3.11
    if len(value) > 5 and value[-5] in "+-" and value[-3] != ":":
        value = value[:-2] + ":" + value[-2:]
    try:
        return datetime.fromisoformat(value).astimezone(tz)
    except ValueError:
        return None


def fetch(route: Route, when: datetime) -> dict:
    search_from = when - timedelta(minutes=1)
    return get_json(API, {
        "from": route.origin,
        "to": route.destination,
        "date": search_from.strftime("%Y-%m-%d"),
        "time": search_from.strftime("%H:%M"),
        "limit": 5,
    })


def _info(conn: dict, tz) -> dict:
    frm, to = conn.get("from") or {}, conn.get("to") or {}
    dep = _parse_ts(frm.get("departure"), tz)
    arr = _parse_ts(to.get("arrival"), tz)
    prognosis = frm.get("prognosis") or {}
    delay = frm.get("delay")
    if delay is None:
        prog_dep = _parse_ts(prognosis.get("departure"), tz)
        if prog_dep and dep:
            delay = int((prog_dep - dep).total_seconds() // 60)
    delay = int(delay or 0)

    platform = (frm.get("platform") or "").strip()
    new_platform = (prognosis.get("platform") or "").strip()
    changed = False
    if platform.endswith("!"):
        platform, changed = platform.rstrip("!"), True
    if new_platform and new_platform != platform:
        platform, changed = new_platform, True

    products = conn.get("products") or []
    line = products[0] if products else ""
    if not line:
        for sec in conn.get("sections") or []:
            j = sec.get("journey")
            if j:
                line = f"{j.get('category', '')}{j.get('number', '')}".strip()
                break
    to_delay = to.get("delay")
    arr_actual = arr + timedelta(minutes=int(to_delay if to_delay is not None else delay)) if arr else None
    return {
        "dep": dep, "arr": arr, "arr_actual": arr_actual, "delay": delay,
        "platform": platform, "platform_changed": changed, "line": line or "",
        "sched_platform": (frm.get("platform") or "").strip().rstrip("!"),
    }


def evaluate(route: Route, dep_hhmm: str, day_key: str, data: dict, tz, state: State,
             notifier: Notifier, lang: str, today) -> None:
    """Compare fresh API data for one departure with what we reported before."""
    key = f"transit:{route.name}:{dep_hhmm}:{day_key}"
    prev = state.get(key) or {"reported_delay": 0, "platform": None, "missing": 0, "missing_reported": False}

    infos = [_info(c, tz) for c in (data.get("connections") or [])]
    mine = next((i for i in infos if i["dep"] and i["dep"].strftime("%H:%M") == dep_hhmm), None)
    fmt = {"route": route.name, "origin": route.origin, "dep": dep_hhmm}

    if mine is None:
        prev["missing"] += 1
        if prev["missing"] >= MISSING_ROUNDS and not prev["missing_reported"]:
            if notifier.send(Message(t(lang, "missing_title", **fmt), t(lang, "missing_body", **fmt),
                                     tags=["warning"], priority=4)):
                prev["missing_reported"] = True
        state.set(key, prev, today)
        return
    prev["missing"] = 0
    fmt["line"] = mine["line"]

    # --- delay -------------------------------------------------------------
    delay, reported = mine["delay"], prev["reported_delay"]
    relevant = delay if delay >= route.min_delay else 0
    msg = None
    if relevant and not reported:
        msg = ("delay_title", 4)
    elif relevant and abs(relevant - reported) >= REPORT_STEP:
        msg = ("delay_update_title", 4 if relevant > reported else 3)
    elif not relevant and reported:
        msg = ("on_time_title", 2)
    if msg:
        title_key, prio = msg
        actual = (mine["dep"] + timedelta(minutes=delay)).strftime("%H:%M")
        if title_key == "on_time_title":
            body = t(lang, "on_time_body", **fmt)
        else:
            body = t(lang, "delay_body", actual=actual, **fmt)
            alt = _alternative(infos, mine, lang)
            if alt:
                body += "\n" + alt
        if notifier.send(Message(t(lang, title_key, delay=delay, **fmt), body,
                                 tags=["train"] if relevant else ["white_check_mark"], priority=prio)):
            prev["reported_delay"] = relevant

    # --- platform ----------------------------------------------------------
    if mine["platform_changed"] and mine["platform"] and mine["platform"] != prev["platform"]:
        old = prev["platform"] or mine["sched_platform"]
        body = (t(lang, "platform_body", platform=mine["platform"], old=old, **fmt)
                if old and old != mine["platform"] else t(lang, "platform_body_new", platform=mine["platform"], **fmt))
        if notifier.send(Message(t(lang, "platform_title", platform=mine["platform"], **fmt), body,
                                 tags=["arrows_counterclockwise"], priority=4)):
            prev["platform"] = mine["platform"]

    state.set(key, prev, today)


def _alternative(infos: list[dict], mine: dict, lang: str) -> str:
    """The next other connection, if it arrives no later than the delayed one."""
    for other in infos:
        if other is mine or not other["dep"] or other["dep"] <= mine["dep"]:
            continue
        if mine["arr_actual"] and other["arr_actual"] and other["arr_actual"] > mine["arr_actual"]:
            return ""
        d = f" (+{other['delay']})" if other["delay"] else ""
        arr = other["arr_actual"].strftime("%H:%M") if other["arr_actual"] else "?"
        return t(lang, "alternative", dep=other["dep"].strftime("%H:%M"), delay=d, arr=arr)
    return ""


class TransitCheck:
    def __init__(self, routes: list[Route], tz, lang: str, fetcher=fetch):
        self.routes, self.tz, self.lang, self.fetcher = routes, tz, lang, fetcher
        self.last_poll: dict[str, datetime] = {}

    def due(self, now: datetime, force: bool = False):
        """Yield (route, 'HH:MM', departure datetime) that should be polled now."""
        day = DAYS[now.weekday()]
        for route in self.routes:
            if day not in route.days and not force:
                continue
            for hhmm in route.departures:
                h, m = map(int, hhmm.split(":"))
                dep = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if force:
                    if dep >= now - timedelta(minutes=5):
                        yield route, hhmm, dep
                    continue
                start = dep - timedelta(minutes=route.watch_minutes)
                # keep watching a delayed train until it has actually left
                end = dep + timedelta(minutes=2)
                if not (start <= now <= end + timedelta(minutes=30)):
                    continue
                pk = f"{route.name}:{hhmm}"
                last = self.last_poll.get(pk)
                if last and now - last < timedelta(minutes=route.poll_minutes):
                    continue
                yield route, hhmm, dep

    def run(self, now: datetime, state: State, notifier: Notifier, force: bool = False) -> None:
        for route, hhmm, dep in list(self.due(now, force)):
            day_key = dep.strftime("%Y-%m-%d")
            key = f"transit:{route.name}:{hhmm}:{day_key}"
            reported = (state.get(key) or {}).get("reported_delay", 0)
            if not force and now > dep + timedelta(minutes=2 + reported):
                continue  # gone
            try:
                data = self.fetcher(route, dep)
            except FetchError as exc:
                log.warning("transit %s %s: %s", route.name, hhmm, exc)
                continue
            self.last_poll[f"{route.name}:{hhmm}"] = now
            evaluate(route, hhmm, day_key, data, self.tz, state, notifier, self.lang, now.date())
