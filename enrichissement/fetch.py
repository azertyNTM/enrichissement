"""Client HTTP poli : user-agent identifiable, robots.txt, une requête par seconde et par hôte."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

MAX_BYTES = 2_000_000


@dataclass
class Page:
    url: str  # URL finale après redirections
    html: str


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class Fetcher:
    def __init__(self, client: httpx.Client, user_agent: str, min_interval: float = 1.0,
                 respect_robots: bool = True):
        self.client = client
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.respect_robots = respect_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    def _throttle(self, host: str) -> None:
        wait = self._last.get(host, 0.0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp: RobotFileParser | None = None
            try:
                self._throttle(parts.netloc)
                r = self.client.get(f"{base}/robots.txt", headers={"User-Agent": self.user_agent})
                if r.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return rp is None or rp.can_fetch(self.user_agent, url)

    def get(self, url: str) -> Page | None:
        if not self._allowed(url):
            return None
        self._throttle(urlsplit(url).netloc)
        try:
            r = self.client.get(url, headers={"User-Agent": self.user_agent,
                                              "Accept-Language": "fr-FR,fr;q=0.9"})
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return None
        return Page(url=str(r.url), html=r.text[:MAX_BYTES])


def make_client(timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, max_redirects=5)
