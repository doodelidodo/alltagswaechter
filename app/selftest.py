"""Offline self-test: every check against recorded-style API answers.

No network, no ntfy. Runs in CI and before a deploy swaps containers.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as config_mod
from .checks import transit, waste, weather
from .i18n import TEXTS, WASTE_TYPES
from .notify import Notifier
from .state import State

TZ = ZoneInfo("Europe/Zurich")
BASE = {"ntfy": {"url": "http://ntfy.invalid", "topic": "test"}}
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))


def notifier() -> Notifier:
    return Notifier(config_mod.Ntfy(url="http://x", topic="t"), dry_run=True)


# --- fixtures in the shape transport.opendata.ch returns ----------------------
def conn(dep: str, arr: str, delay=0, platform="", prognosis_platform=None, line="B 2"):
    return {
        "from": {"departure": f"2026-10-01T{dep}:00+0200", "delay": delay, "platform": platform,
                 "prognosis": {"platform": prognosis_platform, "departure": None}},
        "to": {"arrival": f"2026-10-01T{arr}:00+0200", "delay": None},
        "products": [line],
        "sections": [{"journey": {"category": "B", "number": "2"}}],
    }


def test_config() -> None:
    example = Path(__file__).resolve().parent.parent / "config.example.toml"
    if example.exists():
        cfg = config_mod.load(example)
        check("config: example file parses", cfg.ntfy.topic != "")
        check("config: example has a route", len(cfg.routes) >= 1)
    try:
        config_mod.parse({**BASE, "transit": [{"from": "A", "to": "B", "departures": ["7:09"]}]})
        check("config: rejects 7:09 without leading zero", False)
    except config_mod.ConfigError:
        check("config: rejects 7:09 without leading zero", True)
    try:
        config_mod.parse({"ntfy": {"url": "", "topic": ""}})
        check("config: ntfy is required", False)
    except config_mod.ConfigError:
        check("config: ntfy is required", True)
    cfg = config_mod.parse({**BASE, "transit": [{"from": "A", "to": "B", "departures": ["07:09"], "days": "weekdays"}]})
    check("config: 'weekdays' expands", cfg.routes[0].days == ["mon", "tue", "wed", "thu", "fri"])
    import datetime as dt
    cfg = config_mod.parse({**BASE, "waste": {"schedule": [{"name": "Papier", "dates": [dt.date(2026, 10, 14)]}]}})
    check("config: TOML dates accepted", cfg.waste.schedule[0].dates == ["2026-10-14"])


def test_timestamps() -> None:
    ts = transit._parse_ts("2026-10-01T07:09:00+0200", TZ)
    check("transit: parses +0200 offset", ts is not None and ts.strftime("%H:%M") == "07:09")
    check("transit: bad timestamp is None", transit._parse_ts("nonsense", TZ) is None)


def test_transit_sequence() -> None:
    route = config_mod.Route(name="Arbeit", origin="Erlinsbach, Kilbig", destination="Aarau, Rathausgasse",
                             departures=["07:09"])
    st, n = State(None), notifier()
    today = datetime(2026, 10, 1, tzinfo=TZ).date()

    def step(conns):
        before = len(n.sent)
        transit.evaluate(route, "07:09", "2026-10-01", {"connections": conns}, TZ, st, n, "de", today)
        return n.sent[before:]

    out = step([conn("07:09", "07:21")])
    check("transit: on time → silent", out == [], str([m.title for m in out]))
    out = step([conn("07:09", "07:21", delay=5), conn("07:16", "07:28")])
    check("transit: +5 → one push", len(out) == 1 and "+5" in out[0].title, str([m.title for m in out]))
    check("transit: push names the line", out and out[0].title.startswith("B 2"), out[0].title if out else "")
    out = step([conn("07:09", "07:21", delay=6)])
    check("transit: +6 after +5 → silent (below step)", out == [])
    out = step([conn("07:09", "07:21", delay=9)])
    check("transit: +9 → update push", len(out) == 1 and "+9" in out[0].title, str([m.title for m in out]))
    out = step([conn("07:09", "07:21", delay=1)])
    check("transit: back under threshold → 'pünktlich'", len(out) == 1 and "pünktlich" in out[0].title,
          str([m.title for m in out]))
    out = step([conn("07:09", "07:21", delay=1)])
    check("transit: stays quiet afterwards", out == [])
    out = step([conn("07:09", "07:21", platform="3!")])
    check("transit: platform change '3!' → push", len(out) == 1 and "3" in out[0].title, str([m.title for m in out]))
    check("transit: no 'statt 3' when the old platform is unknown", out and "statt" not in out[0].body,
          out[0].body if out else "")
    out = step([conn("07:09", "07:21", platform="3!")])
    check("transit: platform change only once", out == [])
    out = step([conn("07:16", "07:28")])
    check("transit: missing once → silent (API hiccup)", out == [])
    out = step([conn("07:16", "07:28")])
    check("transit: missing twice → push", len(out) == 1 and "07:09" in out[0].title, str([m.title for m in out]))
    out = step([conn("07:16", "07:28")])
    check("transit: missing reported only once", out == [])


def test_transit_no_bad_alternative() -> None:
    route = config_mod.Route(name="R", origin="A", destination="B", departures=["07:09"])
    n = notifier()
    today = datetime(2026, 10, 1, tzinfo=TZ).date()
    # delayed 3 min, next one departs 07:16 but arrives later → no "Nächste"
    transit.evaluate(route, "07:09", "2026-10-01",
                     {"connections": [conn("07:09", "07:21", delay=3), conn("07:16", "07:28")]},
                     TZ, State(None), n, "de", today)
    n2 = notifier()
    # delayed 12 min → arrives 07:33; the 07:16 arrives 07:28 → suggest it
    transit.evaluate(route, "07:09", "2026-10-01",
                     {"connections": [conn("07:09", "07:21", delay=12), conn("07:16", "07:28")]},
                     TZ, State(None), n2, "de", today)
    check("transit: offers the 07:16 when it arrives earlier", n2.sent and "Nächste: 07:16 → an 07:28" in n2.sent[0].body,
          n2.sent[0].body if n2.sent else "")
    check("transit: no alternative if it arrives later", n.sent and "Nächste" not in n.sent[0].body,
          n.sent[0].body if n.sent else "")


def test_transit_window() -> None:
    route = config_mod.Route(name="R", origin="A", destination="B", departures=["07:09"], watch_minutes=60,
                             poll_minutes=3)
    tc = transit.TransitCheck([route], TZ, "de", fetcher=lambda r, d: {"connections": []})
    thu = datetime(2026, 10, 1, 6, 0, tzinfo=TZ)
    check("transit: 06:00 is before the window", list(tc.due(thu)) == [])
    check("transit: 06:30 is inside", len(list(tc.due(thu.replace(minute=30)))) == 1)
    sat = datetime(2026, 10, 3, 6, 30, tzinfo=TZ)
    check("transit: saturday not watched (weekdays)", list(tc.due(sat)) == [])
    st = State(None)
    tc.run(thu.replace(minute=30), st, notifier())
    check("transit: polls at most every 3 min", list(tc.due(thu.replace(minute=31))) == [])
    check("transit: polls again after 3 min", len(list(tc.due(thu.replace(minute=33)))) == 1)


def hourly(rain=None, temp=None, gust=None):
    times = [f"2026-10-01T{h:02d}:00" for h in range(24)]
    return {"hourly": {"time": times,
                       "precipitation": rain or [0.0] * 24,
                       "temperature_2m": temp or [12.0] * 24,
                       "wind_gusts_10m": gust or [20.0] * 24}}


def test_weather() -> None:
    w = config_mod.Weather(name="Erlinsbach", latitude=47.4, longitude=8.0)
    check("weather: dry day → no push", weather.assess(w, hourly(), "de") is None)
    rain = [0.0] * 24
    rain[16], rain[17], rain[18] = 0.4, 1.2, 0.5
    m = weather.assess(w, hourly(rain=rain), "de")
    check("weather: rain → title 'Regen ab 16 Uhr'", m and m.title == "Regen ab 16 Uhr in Erlinsbach", m.title if m else "")
    check("weather: rain body has window and max", m and "16:00–19:00" in m.body and "1.2 mm/h" in m.body,
          m.body if m else "")
    rain2 = [0.0] * 24
    rain2[21] = 3.0
    check("weather: rain after the window is ignored", weather.assess(w, hourly(rain=rain2), "de") is None)
    temp = [12.0] * 24
    temp[6] = -1.0
    m = weather.assess(w, hourly(temp=temp), "de")
    check("weather: frost in the morning → push", m and "Glätte" in m.body, m.body if m else "")
    gust = [20.0] * 24
    gust[14] = 75.0
    m = weather.assess(w, hourly(gust=gust), "de")
    check("weather: gusts → push", m and "75 km/h" in m.body, m.body if m else "")
    w.always = True
    m = weather.assess(w, hourly(), "en")
    check("weather: always=true sends a calm summary", m and "Nothing special" in m.body, m.body if m else "")


ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20261014\r
SUMMARY:Papier- und Karton\r
 sammlung\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART:20261012T060000\r
SUMMARY:Grüngut\\, Laub\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_waste() -> None:
    ev = waste.parse_ical(ICS)
    check("waste: iCal events parsed", len(ev) == 2, str(ev))
    check("waste: folded summary joined", ev and ev[0][1] == "Papier- und Kartonsammlung", str(ev))
    check("waste: escaped comma", len(ev) > 1 and ev[1][1] == "Grüngut, Laub", str(ev))

    items = [{"key": "cardboard", "station": ""}, {"key": "waste", "station": ""},
             {"key": "mobile", "station": "Stauffacher"}]
    sel = waste.select(items, ["cardboard", "paper"], "de")
    check("waste: type filter by OpenERZ key", [i["key"] for i in sel] == ["cardboard"])
    sel = waste.select([{"key": "Papier- und Kartonsammlung", "station": ""}], ["Karton"], "de")
    check("waste: type filter by word in iCal title", len(sel) == 1)

    rules = [config_mod.WasteRule(name="Grüngut", weekdays=["mon"], skip=["2026-12-28"]),
             config_mod.WasteRule(name="Papier/Karton", dates=["2026-10-14"])]
    import datetime as dt
    check("waste: weekly rule hits monday", len(waste.from_schedule(rules, dt.date(2026, 10, 12))) == 1)
    check("waste: skip date honoured", waste.from_schedule(rules, dt.date(2026, 12, 28)) == [])
    check("waste: explicit date hits", [i["key"] for i in waste.from_schedule(rules, dt.date(2026, 10, 14))]
          == ["Papier/Karton"])
    check("waste: other days empty", waste.from_schedule(rules, dt.date(2026, 10, 13)) == [])

    m = waste.build_message([{"key": "paper", "station": ""}, {"key": "cardboard", "station": ""}],
                            dt.date(2026, 10, 14), "de")
    check("waste: message 'Morgen: Papier und Karton'", m and m.title == "Morgen: Papier und Karton", m.title if m else "")
    check("waste: date reads '14.10.' once", m and "am 14.10. –" in m.body, m.body if m else "")
    m = waste.build_message(waste.from_schedule([config_mod.WasteRule(name="Papier/Karton", dates=["2026-10-15"],
                                                                      note="gebündelt")], dt.date(2026, 10, 15)),
                            dt.date(2026, 10, 15), "de")
    check("waste: note is appended", m and "Papier/Karton: gebündelt" in m.body, m.body if m else "")

    # full check: rules not filtered by types, runs the evening before
    wcfg = config_mod.Waste(types=["cardboard"], schedule=rules)
    wc = waste.WasteCheck(wcfg, "de", openerz=lambda z, d: [], ical=lambda u, d: [])
    n = notifier()
    wc.run(datetime(2026, 10, 11, 19, 0, tzinfo=TZ), n)
    check("waste: sunday evening → 'Morgen: Grüngut'", n.sent and n.sent[0].title == "Morgen: Grüngut",
          str([m.title for m in n.sent]))


def test_daily_scheduling() -> None:
    from .main import Runner
    cfg = config_mod.parse({**BASE, "waste": {"at": "19:00", "schedule": [{"name": "Grüngut", "weekdays": ["mon"]}]}})
    r = Runner(cfg, dry_run=True)
    sun = datetime(2026, 10, 11, 18, 59, tzinfo=TZ)
    r.tick(sun)
    check("daily: nothing before 19:00", r.notifier.sent == [])
    r.tick(sun.replace(hour=19, minute=0))
    check("daily: fires at 19:00", len(r.notifier.sent) == 1)
    r.tick(sun.replace(hour=19, minute=30))
    check("daily: only once per day", len(r.notifier.sent) == 1)
    r2 = Runner(cfg, dry_run=True)
    r2.tick(sun.replace(hour=23, minute=0))
    check("daily: not after the 3 h grace", r2.notifier.sent == [])
    r3 = Runner(cfg, dry_run=True)
    r3.tick(sun.replace(hour=21, minute=0))
    check("daily: a restart at 21:00 still sends", len(r3.notifier.sent) == 1)


def test_i18n() -> None:
    check("i18n: de and en have the same keys", set(TEXTS["de"]) == set(TEXTS["en"]))
    check("i18n: waste names same keys", set(WASTE_TYPES["de"]) == set(WASTE_TYPES["en"]))


def test_state(tmp: Path) -> None:
    p = tmp / "state.json"
    s = State(str(p))
    d = datetime(2026, 10, 1).date()
    s.set("a", {"x": 1}, d)
    check("state: persisted", State(str(p)).get("a") == {"x": 1})
    s.prune(d + timedelta(days=10))
    check("state: pruned after a few days", State(str(p)).get("a") is None)


def run_all() -> int:
    import tempfile
    tests = [test_config, test_timestamps, test_transit_sequence, test_transit_no_bad_alternative,
             test_transit_window, test_weather, test_waste, test_daily_scheduling, test_i18n]
    for fn in tests:
        try:
            fn()
        except Exception:
            check(f"{fn.__name__} crashed", False, traceback.format_exc())
    with tempfile.TemporaryDirectory() as tmp:
        try:
            test_state(Path(tmp))
        except Exception:
            check("test_state crashed", False, traceback.format_exc())

    failed = [r for r in _results if not r[1]]
    for name, ok, detail in _results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"\n     {detail}" if not ok and detail else ""))
    print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())
