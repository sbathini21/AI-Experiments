"""Shared HTTP client: per-host politeness delays + short-lived response cache.

The cache means 500 users asking about the same company in 10 minutes cause one upstream call.
"""
import hashlib
import threading
import time
from urllib.parse import urlparse

import httpx

from ..config import get_settings

# Minimum seconds between requests to a host (respect published fair-use limits).
HOST_MIN_INTERVAL = {
    "api.gdeltproject.org": 5.5,     # GDELT: "one request every 5 seconds"
    "www.sec.gov": 0.15,             # SEC: <=10 req/s
    "data.sec.gov": 0.15,
    "api.openfigi.com": 2.5,         # 25 req/min unauthenticated
    "news.google.com": 1.0,
    "api.search.brave.com": 1.1,     # free tier: 1 req/s
    "api.company-information.service.gov.uk": 0.6,
}

_lock = threading.Lock()
_last_call: dict[str, float] = {}
_cache: dict[str, tuple[float, int, bytes, str]] = {}
_CACHE_MAX = 2000


def _throttle(host: str) -> None:
    gap = HOST_MIN_INTERVAL.get(host, 0.3)
    with _lock:
        now = time.monotonic()
        wait = _last_call.get(host, 0) + gap - now
        _last_call[host] = max(now, _last_call.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


class Response:
    def __init__(self, status: int, content: bytes, content_type: str):
        self.status_code = status
        self.content = content
        self.content_type = content_type

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json
        return json.loads(self.content)


def fetch(url: str, *, method: str = "GET", params: dict | None = None, json_body=None,
          headers: dict | None = None, ttl: int = 600, timeout: float = 25.0) -> Response:
    key = hashlib.sha256(f"{method}|{url}|{sorted((params or {}).items())}|{json_body}".encode()).hexdigest()
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return Response(hit[1], hit[2], hit[3])
    host = urlparse(url).hostname or ""
    _throttle(host)
    h = {"User-Agent": get_settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"}
    h.update(headers or {})
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.request(method, url, params=params, json=json_body, headers=h)
    resp = Response(r.status_code, r.content, r.headers.get("content-type", ""))
    if r.status_code == 200 and ttl > 0:
        if len(_cache) > _CACHE_MAX:
            _cache.clear()
        _cache[key] = (time.time() + ttl, resp.status_code, resp.content, resp.content_type)
    return resp
