import time
import unittest
from unittest.mock import MagicMock, patch

from src.exchange import binance
from src.exchange.types import ExchangeClientError


def _response(status_code: int, payload: dict, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.json.return_value = payload
    resp.text = str(payload)
    resp.headers = headers or {}
    return resp


class TestBinanceRateLimit(unittest.TestCase):
    def setUp(self) -> None:
        binance._rate_limited_until_ms = 0.0
        binance._rate_limit_kind = "ban"
        binance._last_rest_at = 0.0
        self._notify_patch = patch("src.notify.notify_error")
        self._notify_mock = self._notify_patch.start()

    def tearDown(self) -> None:
        self._notify_patch.stop()
        binance._rate_limited_until_ms = 0.0
        binance._rate_limit_kind = "ban"
        binance._last_rest_at = 0.0

    def test_418_ban_no_retry_and_cooldown(self) -> None:
        banned_until = int((time.time() + 300) * 1000)
        resp = _response(
            418,
            {"code": -1003, "msg": f"Way too many requests; IP banned until {banned_until}."},
        )
        with patch("src.exchange.binance.requests.get", return_value=resp) as mock_get:
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
            self.assertEqual(mock_get.call_count, 1)  # no retries

        # Cooldown registered until ban timestamp + REST_BAN_GRACE_SEC
        expected = banned_until + binance._grace_ms()
        self.assertAlmostEqual(
            binance._rate_limited_until_ms, expected, delta=1000
        )
        self.assertEqual(binance._rate_limit_kind, "padded")

        # Next call fails fast without any HTTP request
        with patch("src.exchange.binance.requests.get") as mock_get2:
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "ETHUSDT"})
            mock_get2.assert_not_called()

    def test_padded_ban_notifies_discord(self) -> None:
        until = binance._now_ms() + 60_000
        binance._set_rate_limited_until(until, kind="padded")
        self._notify_mock.assert_called()
        self.assertEqual(self._notify_mock.call_args[0][0], "Binance REST ban")

    def test_grace_does_not_notify_discord(self) -> None:
        self._notify_mock.reset_mock()
        until = binance._now_ms() + 60_000
        binance._set_rate_limited_until(until, kind="grace")
        self._notify_mock.assert_not_called()

    def test_429_backs_off_one_minute_without_retry(self) -> None:
        resp = _response(429, {"code": -1003, "msg": "Too many requests"})
        start_ms = time.time() * 1000
        with patch("src.exchange.binance.requests.get", return_value=resp) as mock_get:
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
            self.assertEqual(mock_get.call_count, 1)
        self.assertGreaterEqual(
            binance._rate_limited_until_ms, start_ms + 59_000 + binance._grace_ms() - 50
        )

    def test_429_respects_retry_after_header(self) -> None:
        resp = _response(
            429, {"code": -1003, "msg": "Too many requests"}, headers={"Retry-After": "120"}
        )
        start_ms = time.time() * 1000
        with patch("src.exchange.binance.requests.get", return_value=resp):
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
        self.assertGreaterEqual(
            binance._rate_limited_until_ms, start_ms + 119_000 + binance._grace_ms() - 50
        )

    def test_rate_limit_error_is_exchange_error(self) -> None:
        # Trading loop catches ExchangeClientError — cooldown must not crash it.
        self.assertTrue(issubclass(binance.RateLimitError, ExchangeClientError))

    def test_normal_error_still_retries(self) -> None:
        resp = _response(500, {"code": -1000, "msg": "Internal error"})
        with (
            patch("src.exchange.binance.requests.get", return_value=resp) as mock_get,
            patch("src.exchange.binance.time.sleep"),
        ):
            with self.assertRaises(ExchangeClientError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
            self.assertEqual(mock_get.call_count, 3)

    def test_legacy_ban_expiry_arms_grace(self) -> None:
        past = time.time() * 1000 - 1000
        binance._rate_limited_until_ms = past
        binance._rate_limit_kind = "ban"
        with (
            patch("src.exchange.binance._persist_rate_limit") as persist,
            patch("src.exchange.binance._clear_persisted_rate_limit") as clear,
            patch("src.exchange.binance.requests.get") as mock_get,
        ):
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
            mock_get.assert_not_called()
            clear.assert_not_called()
            persist.assert_called_once()
            self.assertEqual(binance._rate_limit_kind, "grace")
            self.assertGreater(binance._rate_limited_until_ms, time.time() * 1000)

    def test_padded_ban_expiry_arms_resume(self) -> None:
        past = time.time() * 1000 - 1000
        binance._rate_limited_until_ms = past
        binance._rate_limit_kind = "padded"
        with (
            patch("src.exchange.binance._persist_rate_limit") as persist,
            patch("src.exchange.binance._clear_persisted_rate_limit") as clear,
            patch("src.exchange.binance.requests.get") as mock_get,
        ):
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
            mock_get.assert_not_called()
            clear.assert_not_called()
            persist.assert_called_once()
        self.assertEqual(binance._rate_limit_kind, "resume")
        self.assertGreater(binance.optional_rest_blocked_sec(), 0.0)
        self.assertEqual(binance.rate_limit_remaining_sec(), 0.0)
        self.assertFalse(binance.is_rate_limited())
        self.assertTrue(binance.is_rest_resume())

    def test_resume_blocks_optional_allows_order(self) -> None:
        binance._rate_limited_until_ms = time.time() * 1000 + 60_000
        binance._rate_limit_kind = "resume"
        with patch("src.exchange.binance.requests.get") as mock_get:
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
            mock_get.assert_not_called()
        with (
            patch("src.exchange.binance._ensure_credentials"),
            patch("src.exchange.binance.signed_params", return_value={}),
            patch("src.exchange.binance.auth_headers", return_value={}),
            patch(
                "src.exchange.binance.requests.post",
                return_value=_response(200, {"orderId": 1}),
            ) as mock_post,
        ):
            out = binance._private_post("/fapi/v1/order", {"symbol": "BTCUSDT"})
        self.assertEqual(out["orderId"], 1)
        mock_post.assert_called_once()

    def test_get_order_is_optional_post_is_critical(self) -> None:
        self.assertEqual(
            binance._infer_rest_priority("GET", "/fapi/v1/order"), "optional"
        )
        self.assertEqual(
            binance._infer_rest_priority("POST", "/fapi/v1/order"), "critical"
        )

    def test_resume_blocks_fill_poll_get_order(self) -> None:
        binance._rate_limited_until_ms = time.time() * 1000 + 60_000
        binance._rate_limit_kind = "resume"
        with (
            patch("src.exchange.binance._ensure_credentials"),
            patch("src.exchange.binance.signed_params", return_value={}),
            patch("src.exchange.binance.auth_headers", return_value={}),
            patch("src.exchange.binance.requests.get") as mock_get,
        ):
            with self.assertRaises(binance.RateLimitError):
                binance._private_get(
                    "/fapi/v1/order",
                    {"symbol": "TRXUSDT", "orderId": 1},
                )
            mock_get.assert_not_called()

    def test_fetch_order_detail_skips_rest_when_optional_blocked(self) -> None:
        binance._rate_limited_until_ms = time.time() * 1000 + 60_000
        binance._rate_limit_kind = "resume"
        with (
            patch(
                "src.exchange.binance_ws.get_order_detail_from_ws",
                return_value=None,
            ),
            patch("src.exchange.binance._private_get") as private_get,
        ):
            with self.assertRaises(binance.RateLimitError):
                binance.fetch_order_detail("TRXUSDT", "1")
            private_get.assert_not_called()

    def test_second_thread_skips_http_after_418(self) -> None:
        import threading

        banned_until = int((time.time() + 300) * 1000)
        resp = _response(
            418,
            {"code": -1003, "msg": f"Way too many requests; IP banned until {banned_until}."},
        )
        barrier = threading.Barrier(2)
        results: list[str] = []

        def worker() -> None:
            barrier.wait()
            try:
                binance._public_get("/fapi/v1/income", {})
                results.append("ok")
            except binance.RateLimitError:
                results.append("limited")

        with patch("src.exchange.binance.requests.get", return_value=resp) as mock_get:
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(results, ["limited", "limited"])

    def test_listen_key_1125_no_retry(self) -> None:
        resp = _response(400, {"code": -1125, "msg": "This listenKey does not exist."})
        with (
            patch("src.exchange.binance.requests.put", return_value=resp) as mock_put,
            patch("src.exchange.binance.time.sleep") as mock_sleep,
            patch("src.exchange.binance._ensure_credentials"),
            patch("src.exchange.binance.signed_params", return_value={"listenKey": "dead"}),
            patch("src.exchange.binance.auth_headers", return_value={}),
        ):
            with self.assertRaises(binance.NonRetriableApiError):
                binance._private_request(
                    "PUT",
                    "/fapi/v1/listenKey",
                    {"listenKey": "dead"},
                    max_retries=3,
                )
            self.assertEqual(mock_put.call_count, 1)
            mock_sleep.assert_not_called()

    def test_fetch_positions_skips_rest_when_optional_blocked(self) -> None:
        binance._rate_limited_until_ms = time.time() * 1000 + 60_000
        binance._rate_limit_kind = "resume"
        with (
            patch("src.exchange.binance_ws.watch_symbols"),
            patch(
                "src.exchange.binance_ws.get_symbol_positions_from_ws",
                return_value=None,
            ),
            patch(
                "src.exchange.binance_ws.get_symbol_positions_lenient",
                return_value=None,
            ),
            patch("src.exchange.binance.fetch_symbol_positions_rest") as rest,
        ):
            with self.assertRaises(binance.RateLimitError):
                binance.fetch_symbol_positions("BTCUSDT")
            rest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
