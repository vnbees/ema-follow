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

    def tearDown(self) -> None:
        binance._rate_limited_until_ms = 0.0

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

        # Cooldown registered until the ban timestamp
        self.assertAlmostEqual(
            binance._rate_limited_until_ms, banned_until, delta=1000
        )

        # Next call fails fast without any HTTP request
        with patch("src.exchange.binance.requests.get") as mock_get2:
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "ETHUSDT"})
            mock_get2.assert_not_called()

    def test_429_backs_off_one_minute_without_retry(self) -> None:
        resp = _response(429, {"code": -1003, "msg": "Too many requests"})
        start_ms = time.time() * 1000
        with patch("src.exchange.binance.requests.get", return_value=resp) as mock_get:
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
            self.assertEqual(mock_get.call_count, 1)
        self.assertGreaterEqual(binance._rate_limited_until_ms, start_ms + 59_000)

    def test_429_respects_retry_after_header(self) -> None:
        resp = _response(
            429, {"code": -1003, "msg": "Too many requests"}, headers={"Retry-After": "120"}
        )
        start_ms = time.time() * 1000
        with patch("src.exchange.binance.requests.get", return_value=resp):
            with self.assertRaises(binance.RateLimitError):
                binance._public_get("/fapi/v1/klines", {"symbol": "BTCUSDT"})
        self.assertGreaterEqual(binance._rate_limited_until_ms, start_ms + 119_000)

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


if __name__ == "__main__":
    unittest.main()
