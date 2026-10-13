import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from auth.opencode_catalog import static_snapshot
from auth.token_manager import TokenManager
from backend.opencode import OpenCodeClient, fold_sse_to_message
from backend.proxy_pool import ProxyPool, mask_proxy, validate_proxy_url
from test_opencode_client import FakeResponse, make_token_manager, sse_lines

PROXY_A = "http://user:secret@10.0.0.1:3128"
PROXY_B = "socks5://user:secret@10.0.0.2:1080"


class ProxySessionStub:
    """Records the proxy= each post() went through and answers per route."""

    def __init__(self, answers):
        self.answers = answers
        self.routes = []

    async def post(self, url, json=None, headers=None, proxy=None):
        self.routes.append(proxy)
        response = self.answers[len(self.routes) - 1]
        response.headers = {}  # a real aiohttp response always has them
        return response

    async def close(self):
        pass


def pool_with(proxies):
    tm = make_token_manager()
    tm.get_proxies.return_value = list(proxies)
    return ProxyPool(tm)


class TestProxyPool(unittest.TestCase):
    def test_validation_and_masking(self):
        self.assertEqual(validate_proxy_url(f" {PROXY_B} "), PROXY_B)
        for bad in ("", "10.0.0.1:80", "ftp://h:1", "http://host"):
            with self.assertRaises(ValueError):
                validate_proxy_url(bad)
        self.assertNotIn("secret", mask_proxy(PROXY_A))
        self.assertEqual(mask_proxy(None), "direct")

    def test_direct_only_without_proxies(self):
        self.assertEqual(pool_with([]).routes(), [None])

    def test_limited_route_is_skipped_and_last_good_goes_first(self):
        pool = pool_with([PROXY_A, PROXY_B])
        self.assertEqual(pool.routes(), [None, PROXY_A, PROXY_B])
        pool.mark_limited(None, 60)
        self.assertEqual(pool.routes(), [PROXY_A, PROXY_B])
        pool.mark_good(PROXY_B)
        self.assertEqual(pool.routes(), [PROXY_B, PROXY_A])
        self.assertTrue(pool.has_alternative(PROXY_B))

    def test_all_limited_still_offers_the_soonest_route(self):
        pool = pool_with([PROXY_A])
        pool.mark_limited(None, 500)
        pool.mark_limited(PROXY_A, 100)
        self.assertEqual(pool.routes(), [PROXY_A])
        self.assertFalse(pool.has_alternative(PROXY_A))

    def test_expired_cooldown_frees_the_route(self):
        pool = pool_with([PROXY_A])
        pool.mark_limited(None, 60)
        pool._until[None] = time.time() - 1
        self.assertEqual(pool.routes(), [None, PROXY_A])

    def test_http_proxy_is_a_request_argument_and_socks_gets_its_own_session(self):
        pool = pool_with([PROXY_A, PROXY_B])
        default = object()
        self.assertEqual(pool.session_for(None, default), (default, {}))
        self.assertEqual(pool.session_for(PROXY_A, default), (default, {"proxy": PROXY_A}))

    def test_token_manager_stores_proxies(self):
        with tempfile.TemporaryDirectory() as d:
            tm = TokenManager(storage_path=Path(d) / "accounts.json")
            self.assertTrue(tm.add_proxy(PROXY_A))
            self.assertFalse(tm.add_proxy(PROXY_A))
            self.assertEqual(TokenManager(storage_path=Path(d) / "accounts.json").get_proxies(), [PROXY_A])
            self.assertTrue(tm.remove_proxy(PROXY_A))
            self.assertFalse(tm.remove_proxy(PROXY_A))


class TestClientRotation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("backend.opencode.get_catalog", side_effect=lambda token=None, ua="": static_snapshot())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_turn(self, answers, proxies):
        session = ProxySessionStub(answers)
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session,
                                proxy_pool=pool_with(proxies))
        req = {"model": "space-bunny-free", "messages": [{"role": "user", "content": "x"}]}
        status, msg = await fold_sse_to_message(client.generate_stream(req))
        return status, msg, session, client

    async def test_429_moves_the_same_turn_to_the_next_ip(self):
        ok = FakeResponse(200, sse_lines([{"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}]))
        status, msg, session, client = await self.run_turn(
            [FakeResponse(429, body="rate limited"), ok], [PROXY_A])
        self.assertEqual(status, 200)
        self.assertEqual(session.routes, [None, PROXY_A])
        self.assertEqual(msg["content"][0]["text"], "hi")
        # The next turn starts on the IP that worked, and the throttled one rests
        self.assertEqual(client.proxy_pool.routes()[0], PROXY_A)
        self.assertNotIn(None, client.proxy_pool.routes())

    async def test_unreachable_proxy_is_skipped(self):
        import aiohttp
        ok = FakeResponse(200, sse_lines([{"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}]))

        class Flaky(ProxySessionStub):
            async def post(self, url, json=None, headers=None, proxy=None):
                self.routes.append(proxy)
                if proxy is None:
                    raise aiohttp.ClientConnectionError("down")
                ok.headers = {}
                return ok

        session = Flaky([])
        client = OpenCodeClient(make_token_manager([{"email": "a@b"}]), http_session=session,
                                proxy_pool=pool_with([PROXY_A]))
        status, msg = await fold_sse_to_message(client.generate_stream(
            {"model": "space-bunny-free", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual((status, session.routes), (200, [None, PROXY_A]))

    async def test_last_route_429_reaches_the_account_handling(self):
        status, msg, session, _ = await self.run_turn(
            [FakeResponse(429, body="a"), FakeResponse(429, body="b")], [PROXY_A])
        self.assertEqual(status, 429)
        self.assertEqual(session.routes, [None, PROXY_A])

    async def test_without_proxies_a_429_is_not_retried(self):
        status, _, session, _ = await self.run_turn([FakeResponse(429, body="a")], [])
        self.assertEqual((status, session.routes), (429, [None]))


if __name__ == "__main__":
    unittest.main()
