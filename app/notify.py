"""Send notifications to ntfy (self-hosted or ntfy.sh)."""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

from .config import Ntfy
from .http import FetchError, post_json

log = logging.getLogger(__name__)


@dataclass
class Message:
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    priority: int = 3  # ntfy: 1 min … 5 urgent
    click: str = ""


class Notifier:
    def __init__(self, cfg: Ntfy, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.sent: list[Message] = []  # for tests and --dry-run output

    def _headers(self) -> dict:
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        if self.cfg.username:
            raw = f"{self.cfg.username}:{self.cfg.password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {}

    def send(self, msg: Message) -> bool:
        self.sent.append(msg)
        if self.dry_run:
            log.info("[dry-run] %s — %s", msg.title, msg.body.replace("\n", " / "))
            return True
        payload = {
            "topic": self.cfg.topic,
            "title": msg.title,
            "message": msg.body,
            "priority": msg.priority,
        }
        if msg.tags:
            payload["tags"] = msg.tags
        if msg.click:
            payload["click"] = msg.click
        try:
            post_json(self.cfg.url, payload, self._headers())
            log.info("sent: %s", msg.title)
            return True
        except FetchError as exc:
            # Never crash the loop because a notification failed; it is retried
            # next round, because the check only records state after success.
            log.warning("ntfy failed: %s", exc)
            return False
