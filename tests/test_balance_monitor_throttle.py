import unittest
from unittest.mock import patch

import src.main as main_mod
from src.exchange.types import FuturesAccountBalance


def _bal() -> FuturesAccountBalance:
    return FuturesAccountBalance(
        margin_coin="USDT",
        available=100.0,
        account_equity=110.0,
        usdt_equity=110.0,
        total_maint_margin=1.0,
        total_initial_margin=2.0,
    )


class TestBalanceMonitorThrottle(unittest.TestCase):
    def setUp(self) -> None:
        main_mod._last_balance_monitor_rest_at = 0.0
        main_mod._last_spot_snapshot_at = 0.0

    @patch("src.main.refresh_account_profit_info")
    @patch("src.main.refresh_margin_dashboard_fields")
    @patch("src.main.update_account_balance")
    @patch("src.main.db.insert_equity_snapshot")
    @patch("src.main.db.insert_spot_snapshot")
    @patch("src.main.fetch_spot_balance", return_value=5.0)
    @patch("src.main.fetch_futures_balance")
    @patch("src.main.has_credentials", return_value=True)
    def test_skips_forced_rest_when_recent(
        self,
        _creds,
        fetch_bal,
        fetch_spot,
        insert_spot,
        _eq,
        _upd,
        _margin,
        _pnl,
    ):
        fetch_bal.return_value = _bal()
        # Far-future timestamps → neither monitor REST nor spot is due.
        main_mod._last_balance_monitor_rest_at = 1e12
        main_mod._last_spot_snapshot_at = 1e12
        with (
            patch("src.main.BALANCE_MONITOR_REST_SEC", 900.0),
            patch("src.main.SPOT_SNAPSHOT_INTERVAL_SEC", 900.0),
            patch("src.exchange.binance.is_rate_limited", return_value=False),
            patch("src.exchange.binance.fetch_futures_balance_rest") as rest,
        ):
            main_mod.log_futures_balance_once("BTCUSDT", managed_symbols=["BTCUSDT"])
        rest.assert_not_called()
        fetch_bal.assert_called_once()
        fetch_spot.assert_not_called()
        insert_spot.assert_not_called()

    @patch("src.main.refresh_account_profit_info")
    @patch("src.main.refresh_margin_dashboard_fields")
    @patch("src.main.update_account_balance")
    @patch("src.main.db.insert_equity_snapshot")
    @patch("src.main.db.insert_spot_snapshot")
    @patch("src.main.fetch_spot_balance", return_value=5.0)
    @patch("src.main.fetch_futures_balance")
    @patch("src.main.has_credentials", return_value=True)
    def test_spot_and_rest_run_when_due(
        self,
        _creds,
        fetch_bal,
        fetch_spot,
        insert_spot,
        _eq,
        _upd,
        _margin,
        _pnl,
    ):
        rest_bal = _bal()
        fetch_bal.return_value = rest_bal
        with (
            patch("src.main.BALANCE_MONITOR_REST_SEC", 1.0),
            patch("src.main.SPOT_SNAPSHOT_INTERVAL_SEC", 1.0),
            patch("src.exchange.binance.is_rate_limited", return_value=False),
            patch(
                "src.exchange.binance.fetch_futures_balance_rest",
                return_value=rest_bal,
            ) as rest,
            patch("src.exchange.binance_ws.cache.CACHE.set_balance") as set_bal,
        ):
            main_mod._last_balance_monitor_rest_at = 0.0
            main_mod._last_spot_snapshot_at = 0.0
            main_mod.log_futures_balance_once("BTCUSDT", managed_symbols=["BTCUSDT"])
        rest.assert_called_once_with("BTCUSDT")
        set_bal.assert_called_once_with(rest_bal)
        fetch_bal.assert_not_called()
        fetch_spot.assert_called_once()
        insert_spot.assert_called_once_with(5.0)
        self.assertGreater(main_mod._last_balance_monitor_rest_at, 0.0)
        self.assertGreater(main_mod._last_spot_snapshot_at, 0.0)

    @patch("src.main.refresh_account_profit_info")
    @patch("src.main.refresh_margin_dashboard_fields")
    @patch("src.main.update_account_balance")
    @patch("src.main.db.insert_equity_snapshot")
    @patch("src.main.db.insert_spot_snapshot")
    @patch("src.main.fetch_spot_balance", return_value=5.0)
    @patch("src.main.fetch_futures_balance")
    @patch("src.main.has_credentials", return_value=True)
    def test_skips_rest_while_rate_limited(
        self,
        _creds,
        fetch_bal,
        fetch_spot,
        insert_spot,
        _eq,
        _upd,
        _margin,
        _pnl,
    ):
        fetch_bal.return_value = _bal()
        main_mod._last_balance_monitor_rest_at = 0.0
        main_mod._last_spot_snapshot_at = 0.0
        with (
            patch("src.main.BALANCE_MONITOR_REST_SEC", 1.0),
            patch("src.main.SPOT_SNAPSHOT_INTERVAL_SEC", 1.0),
            patch("src.exchange.binance.is_rate_limited", return_value=True),
            patch("src.exchange.binance.fetch_futures_balance_rest") as rest,
        ):
            main_mod.log_futures_balance_once("BTCUSDT", managed_symbols=["BTCUSDT"])
        rest.assert_not_called()
        fetch_bal.assert_called_once()
        fetch_spot.assert_not_called()
        insert_spot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
