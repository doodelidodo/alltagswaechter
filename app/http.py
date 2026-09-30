"""Tiny HTTP helper on top of urllib, so the image needs no dependencies."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

USER_AGENT = f"alltagswaechter/{__version__} (+https://github.com/doodelidodo/alltagswaechter)"
TIMEOUT = 20


class FetchError(RuntimeError):
    pass


def get(url: str, params: dict | list | None = None) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", "replace")
        raise FetchError(f"HTTP {exc.code} from {url}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{url}: {exc}") from exc


def get_json(url: str, params: dict | list | None = None):
    raw = get(url, params)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url}: invalid JSON ({exc})") from exc


def post_json(url: str, payload: dict, headers: dict | None = None) -> int:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{url}: {exc}") from exc
