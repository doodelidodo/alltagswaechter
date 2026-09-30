"""Entry point: the scheduler loop and a few helper commands.

    python -m app                      run forever (the container default)
    python -m app check-config         validate the config and show what is watched
    python -m app test-notify          send one test push
    python -m app now [transit|weather|waste|all] [--dry-run]
                                       run checks immediately, ignoring the clock
    python -m app stations "Aarau"     look up exact stop names
    python -m app selftest             offline tests (used by CI and the image build)
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .checks.transit import TransitCheck
from .checks.waste import WasteCheck
from .checks.weather import WeatherCheck
from .config import DAYS, Config, ConfigError, load
from .http import FetchError, get_json
from .i18n import t
from .notify import Message, Notifier
from .state import State

log = logging.getLogger("alltagswaechter")
TICK_SECONDS = 30
DAILY_GRACE = timedelta(hours=3)   # a daily job missed by a restart still runs within this window
HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/alltagswaechter.heartbeat"))


class Runner:
    def __init__(self, cfg: Config, dry_run: bool = False):
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)
        self.state = State(None if dry_run else cfg.state_file)
        self.notifier = Notifier(cfg.ntfy, dry_run=dry_run)
        self.transit = TransitCheck(cfg.routes, self.tz, cfg.language) if cfg.routes else None
        self.weather = WeatherCheck(cfg.weather, cfg.timezone, cfg.language) if cfg.weather else None
        self.waste = WasteCheck(cfg.waste, cfg.language) if cfg.waste else None

    def _daily(self, name: str, at: str, days: list[str] | None, now: datetime, job) -> None:
        if days is not None and DAYS[now.weekday()] not in days:
            return
        h, m = map(int, at.split(":"))
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if not (due <= now <= due + DAILY_GRACE):
            return
        key = f"daily:{name}:{now.date().isoformat()}"
        if self.state.get(key):
            return
        if job(now, self.notifier):
            self.state.set(key, True, now.date())

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.tz)
        self.state.prune(now.date())
        jobs = []
        if self.transit:
            jobs.append(("transit", lambda: self.transit.run(now, self.state, self.notifier)))
        if self.weather:
            w = self.weather.w
            jobs.append(("weather", lambda: self._daily("weather", w.at, w.days, now, self.weather.run)))
        if self.waste:
            jobs.append(("waste", lambda: self._daily("waste", self.cfg.waste.at, None, now, self.waste.run)))
        for name, job in jobs:
            try:
                job()
            except Exception:  # one broken check must not stop the others
                log.exception("check %s failed", name)
        try:
            HEARTBEAT.write_text(now.isoformat())
        except OSError:
            pass

    def now(self, which: str) -> None:
        now = datetime.now(self.tz)
        if which in ("transit", "all") and self.transit:
            self.transit.run(now, self.state, self.notifier, force=True)
        if which in ("weather", "all") and self.weather:
            self.weather.run(now, self.notifier)
        if which in ("waste", "all") and self.waste:
            self.waste.run(now, self.notifier)


def describe(cfg: Config) -> str:
    lines = [f"ntfy: {cfg.ntfy.url} topic={cfg.ntfy.topic} "
             f"auth={'token' if cfg.ntfy.token else 'user' if cfg.ntfy.username else 'none'}",
             f"language={cfg.language} timezone={cfg.timezone} state={cfg.state_file}"]
    for r in cfg.routes:
        lines.append(f"transit: {r.name}: {r.origin} → {r.destination} at {', '.join(r.departures)} "
                     f"on {','.join(r.days)} (watch {r.watch_minutes} min, every {r.poll_minutes}, ≥{r.min_delay} min)")
    if cfg.weather:
        w = cfg.weather
        lines.append(f"weather: {w.name or ''} ({w.latitude}, {w.longitude}) at {w.at}, window {w.window[0]}–{w.window[1]}, "
                     f"rain ≥{w.rain_mm} mm/h, gusts ≥{w.gust_kmh} km/h, frost ≤{w.frost_c} °C, model {w.model or 'auto'}")
    if cfg.waste:
        w = cfg.waste
        src = [s for s in (f"OpenERZ {w.openerz_zip}" if w.openerz_zip else "", "iCal" if w.ical_url else "") if s]
        src += [f"{r.name} ({len(r.dates)} dates{', every ' + '/'.join(r.weekdays) if r.weekdays else ''})" for r in w.schedule]
        lines.append(f"waste: reminder at {w.at} the evening before; sources: {'; '.join(src)}"
                     + (f"; types {', '.join(w.types)}" if w.types else ""))
    return "\n".join(lines)


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def cmd_stations(query: str) -> int:
    try:
        data = get_json("https://transport.opendata.ch/v1/locations", {"query": query, "type": "station"})
    except FetchError as exc:
        print(exc, file=sys.stderr)
        return 1
    for s in data.get("stations") or []:
        if s.get("id"):
            print(f"{s['id']:>8}  {s['name']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alltagswaechter", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "check-config", "test-notify", "now", "stations", "selftest", "version"])
    parser.add_argument("arg", nargs="?", default="all")
    parser.add_argument("--config", default=os.environ.get("CONFIG", "/config/config.toml"))
    parser.add_argument("--dry-run", action="store_true", help="print notifications instead of sending")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "selftest":
        from .selftest import run_all
        return run_all()
    if args.command == "stations":
        return cmd_stations(args.arg if args.arg != "all" else "")

    try:
        cfg = load(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if args.command == "check-config":
        print(describe(cfg))
        return 0

    runner = Runner(cfg, dry_run=args.dry_run)
    if args.command == "test-notify":
        ok = runner.notifier.send(Message(t(cfg.language, "test_title"), t(cfg.language, "test_body"),
                                          tags=["wave"], priority=3))
        return 0 if ok else 1
    if args.command == "now":
        runner.now(args.arg)
        if args.dry_run and not runner.notifier.sent:
            print("nothing to report")
        return 0

    log.info("alltagswaechter %s starting\n%s", __version__, describe(cfg))
    stop = {"flag": False}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.update(flag=True))
    mtime = _mtime(args.config)
    while not stop["flag"]:
        # Edit the config and it applies within 30 s – no restart needed. A
        # broken edit is reported and the previous config keeps running.
        current = _mtime(args.config)
        if current != mtime:
            mtime = current
            try:
                cfg = load(args.config)
                runner = Runner(cfg, dry_run=args.dry_run)
                log.info("config reloaded\n%s", describe(cfg))
            except ConfigError as exc:
                log.error("config change ignored, keeping the previous one: %s", exc)
        runner.tick()
        for _ in range(TICK_SECONDS):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("stopped")
    return 0
