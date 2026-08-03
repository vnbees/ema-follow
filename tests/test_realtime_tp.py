import unittest
from unittest.mock import patch

from src.realtime_tp import _run_once, _side_has_tp_candidate, get_realtime_tp_status


def _lot(**kwargs) -> dict:
    base = {
        "id": 1,
        "symbol": "BTCUSDT",
        "long_status": "open",
        "long_entry": 100.0,
        "long_size": 1.0,
        "short_status": "closed",
        "short_entry": 0.0,
        "short_size": 0.0,
    }
    base.update(kwargs)
    return base


class TestSideHasTpCandidate(unittest.TestCase):
    @patch("src.realtime_tp.db.get_open_pair_lots")
    def test_long_at_tp(self, get_lots):
        get_lots.return_value = [_lot()]
        self.assertTrue(_side_has_tp_candidate("BTCUSDT", "long", 101.0, 1.0))

    @patch("src.realtime_tp.db.get_open_pair_lots")
    def test_long_below_tp(self, get_lots):
        get_lots.return_value = [_lot()]
        self.assertFalse(_side_has_tp_candidate("BTCUSDT", "long", 100.5, 1.0))

    @patch("src.realtime_tp.db.get_open_pair_lots")
    def test_closed_leg_ignored(self, get_lots):
        get_lots.return_value = [_lot(long_status="closed")]
        self.assertFalse(_side_has_tp_candidate("BTCUSDT", "long", 200.0, 1.0))


class TestRunOnce(unittest.TestCase):
    @patch("src.rsi_trading._scan_take_profits_locked", return_value=True)
    @patch("src.margin_guard.effective_tp_pct", return_value=1.0)
    @patch("src.exchange.binance_ws.get_mark_from_ws", return_value=101.5)
    @patch("src.exchange.binance.is_rate_limited", return_value=False)
    @patch("src.realtime_tp.has_credentials", return_value=True)
    @patch("src.rsi_trading._trading_enabled", return_value=True)
    @patch("src.realtime_tp.db.get_open_pair_lots")
    @patch("src.realtime_tp.db.get_all_open_pair_lots")
    def test_closes_when_mark_hits_tp(
        self,
        all_lots,
        sym_lots,
        _trading,
        _creds,
        _rate,
        _mark,
        _tp,
        scan_locked,
    ):
        all_lots.return_value = [_lot()]
        sym_lots.return_value = [_lot()]
        _run_once()
        scan_locked.assert_called_once()
        kwargs = scan_locked.call_args.kwargs
        self.assertEqual(kwargs["trigger"], "realtime")
        self.assertFalse(kwargs["reopen_pair"])
        self.assertEqual(kwargs["tp_target_pct"], 1.0)
        self.assertGreaterEqual(get_realtime_tp_status()["closes"], 1)

    @patch("src.rsi_trading._scan_take_profits_locked")
    @patch("src.margin_guard.effective_tp_pct", return_value=1.0)
    @patch("src.exchange.binance_ws.get_mark_from_ws", return_value=100.5)
    @patch("src.exchange.binance.is_rate_limited", return_value=False)
    @patch("src.realtime_tp.has_credentials", return_value=True)
    @patch("src.rsi_trading._trading_enabled", return_value=True)
    @patch("src.realtime_tp.db.get_open_pair_lots")
    @patch("src.realtime_tp.db.get_all_open_pair_lots")
    def test_no_close_below_threshold(
        self,
        all_lots,
        sym_lots,
        _trading,
        _creds,
        _rate,
        _mark,
        _tp,
        scan_locked,
    ):
        all_lots.return_value = [_lot()]
        sym_lots.return_value = [_lot()]
        _run_once()
        scan_locked.assert_not_called()

    @patch("src.rsi_trading._scan_take_profits_locked")
    @patch("src.margin_guard.effective_tp_pct", return_value=1.0)
    @patch("src.exchange.binance_ws.get_mark_from_ws", return_value=None)
    @patch("src.exchange.binance.is_rate_limited", return_value=False)
    @patch("src.realtime_tp.has_credentials", return_value=True)
    @patch("src.rsi_trading._trading_enabled", return_value=True)
    @patch("src.realtime_tp.db.get_all_open_pair_lots")
    def test_ws_stale_pauses_watcher(
        self,
        all_lots,
        _trading,
        _creds,
        _rate,
        _mark,
        _tp,
        scan_locked,
    ):
        all_lots.return_value = [_lot()]
        _run_once()
        scan_locked.assert_not_called()
        status = get_realtime_tp_status()
        self.assertIsNotNone(status["paused_reason"])
        self.assertIn("ws", status["paused_reason"])

    @patch("src.rsi_trading._scan_take_profits_locked")
    @patch("src.realtime_tp.has_credentials", return_value=True)
    @patch("src.rsi_trading._trading_enabled", return_value=False)
    def test_trading_disabled_pauses_watcher(self, _trading, _creds, scan_locked):
        _run_once()
        scan_locked.assert_not_called()
        self.assertEqual(get_realtime_tp_status()["paused_reason"], "trading disabled")


if __name__ == "__main__":
    unittest.main()
