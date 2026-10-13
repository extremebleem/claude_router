"""
Outbound proxy pool: when one IP is rate limited, the request moves to the next one.

A route is a proxy URL, or None for the direct connection, which always takes part so the pool
never makes things worse than having no proxies. A route that was throttled cools down for a while
and the last route that worked is tried first, so a healthy IP is not left every turn.
"""

import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from logger import log

PROXY_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4")
DEFAULT_COOLDOWN_SECONDS = 600.0
FAILURE_COOLDOWN_SECONDS = 60.0  # a dead proxy is retried sooner than a rate limited one

Route = Optional[str]


def validate_proxy_url(url: str) -> str:
    """Returns the normalised URL or raises ValueError."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in PROXY_SCHEMES or not parsed.hostname or not parsed.port:
        raise ValueError(f"expected <scheme>://[user:pass@]host:port with scheme in {', '.join(PROXY_SCHEMES)}")
    return url


def mask_proxy(url: Route) -> str:
    """Proxy URL without its credentials, safe to print or send to the dashboard."""
    if url is None:
        return "direct"
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"


class ProxyPool:
    def __init__(self, token_manager: Any = None):
        self.token_manager = token_manager
        self._until: Dict[Route, float] = {}
        self._current: Route = None
        self._socks_sessions: Dict[str, aiohttp.ClientSession] = {}

    def proxies(self) -> List[str]:
        return self.token_manager.get_proxies() if self.token_manager else []

    def _all_routes(self) -> List[Route]:
        routes: List[Route] = [None, *self.proxies()]
        # Start from the route that worked last
        if self._current in routes:
            i = routes.index(self._current)
            routes = routes[i:] + routes[:i]
        return routes

    def routes(self) -> List[Route]:
        """Routes to try for a request, best first. With no proxies configured that is just [None].
        When every route is cooling down the one that frees up first is still returned, so a request
        is never refused only because of the pool's own bookkeeping."""
        all_routes = self._all_routes()
        now = time.time()
        free = [r for r in all_routes if self._until.get(r, 0) <= now]
        return free or [min(all_routes, key=lambda r: self._until.get(r, 0))]

    def has_alternative(self, route: Route) -> bool:
        """True when some other route is not cooling down, i.e. a retry elsewhere can help."""
        now = time.time()
        return any(r != route and self._until.get(r, 0) <= now for r in self._all_routes())

    def mark_limited(self, route: Route, seconds: Optional[float] = None, failed: bool = False):
        seconds = seconds or (FAILURE_COOLDOWN_SECONDS if failed else DEFAULT_COOLDOWN_SECONDS)
        self._until[route] = time.time() + seconds
        log(f"[ProxyPool] {mask_proxy(route)} {'unreachable' if failed else 'rate limited'}, "
            f"cooling down {int(seconds)}s", level="WARN")

    def mark_good(self, route: Route):
        self._current = route

    def status(self) -> List[Dict[str, Any]]:
        now = time.time()
        return [
            {"proxy": mask_proxy(r), "cooldown": max(0, int(self._until.get(r, 0) - now)), "current": r == self._current}
            for r in [None, *self.proxies()]
        ]

    def session_for(self, route: Route, default: aiohttp.ClientSession) -> Tuple[aiohttp.ClientSession, Dict[str, Any]]:
        """Session and extra post() arguments that send a request through the route.
        HTTP(S) proxies are a per-request argument of the shared session; SOCKS needs its own
        connector, so those sessions are kept per proxy and closed with the pool."""
        if route is None:
            return default, {}
        if urlparse(route).scheme.startswith("socks"):
            session = self._socks_sessions.get(route)
            if session is None:
                try:
                    from aiohttp_socks import ProxyConnector
                except ImportError as e:
                    raise aiohttp.ClientError("SOCKS proxy needs 'pip install aiohttp-socks'") from e
                session = aiohttp.ClientSession(
                    connector=ProxyConnector.from_url(route), timeout=default.timeout
                )
                self._socks_sessions[route] = session
            return session, {}
        return default, {"proxy": route}

    async def close(self):
        for session in self._socks_sessions.values():
            await session.close()
        self._socks_sessions.clear()
