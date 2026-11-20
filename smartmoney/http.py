"""HTTP client with per-host rate limiting, retries and polite identification."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import requests

from . import config

_RATE = {"sec.gov": config.SEC_MAX_RPS}


class _Limiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def wait(self, host: str) -> None:
        key = next((k for k in _RATE if host.endswith(k)), None)
        if key is None:
            return
        interval = 1.0 / _RATE[key]
        with self._lock:
            now = time.monotonic()
            t = max(now, self._next.get(key, 0.0))
            self._next[key] = t + interval
        if t > now:
            time.sleep(t - now)


_limiter = _Limiter()
_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        _local.s = s
    return s


def get(url: str, *, params: dict | None = None, timeout: float = 60, retries: int = 4,
        headers: dict | None = None, ok_404: bool = False) -> requests.Response | None:
    host = urlparse(url).netloc
    h = {"User-Agent": config.SEC_USER_AGENT if "sec.gov" in host else config.YAHOO_USER_AGENT,
         "Accept-Encoding": "gzip, deflate"}
    if headers:
        h.update(headers)
    last: Exception | None = None
    for attempt in range(retries + 1):
        _limiter.wait(host)
        try:
            r = _session().get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 404 and ok_404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} for {url}")
            r.raise_for_status()
            return r
        except (requests.RequestException,) as e:  # noqa: PERF203
            last = e
            if attempt < retries:
                time.sleep(min(30, 2 ** attempt + 0.5))
    raise RuntimeError(f"GET failed after {retries + 1} attempts: {url}: {last}")
