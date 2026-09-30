"""Small persistent key/value state so nothing is reported twice.

Survives restarts (JSON file on a volume). Entries carry a date and are pruned
after a few days, so the file never grows.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
KEEP_DAYS = 3


class State:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.data: dict[str, dict] = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("state file unreadable, starting fresh: %s", exc)

    def get(self, key: str):
        entry = self.data.get(key)
        return entry["value"] if entry else None

    def set(self, key: str, value, day: date) -> None:
        self.data[key] = {"value": value, "day": day.isoformat()}
        self._save()

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        old = [k for k, v in self.data.items() if v.get("day", "") < cutoff]
        for k in old:
            del self.data[k]
        if old:
            self._save()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.path)
