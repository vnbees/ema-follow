import unittest
from unittest.mock import MagicMock, patch

from src.exchange.types import FuturesAccountBalance
from src.margin_preflight import (
    LegCandidate,
    PairCandidate,
    collect_leg_candidates,
    ensure_available_for_pair,
    pick_best_leg,
    pick_best_pair,
    required_available_for_pair,
)
from src.rsi import RsiSnapshot
from src.rsi_trading import _open_pair
from src.exchange import ExchangeClientError


class TestRequiredAvailable(unittest.TestCase):
    def test_buffer_applied(self):
        # equity 200 -> margin/leg 1 USDT -> 2 * 1 * 1.1 = 2.2
        with patch("src.margin_preflight.MARGIN_PREFLIGHT_BUFFER_PCT", 10.0):
            self.assertAlmostEqual(required_available_for_pair(200.0), 2.2)


class TestPickBest(unittest.TestCase):
    def test_sort_legs_by_pnl(self):
        legs = [
            LegCandidate("A", "long", 1, 1.0, 100.0, -2.0),
            LegCandidate("B", "long", 2, 1.0, 100.0, 0.5),
            LegCandidate("C", "short", 3, 1.0, 100.0, -0.1),
        ]
        legs.sort(key=lambda row: row.pnl_est, reverse=True)
        self.assertEqual(legs[0].pnl_est, 0.5)
        self.assertEqual(legs[1].pnl_est, -0.1)
        self.assertEqual(legs[2].pnl_est, -2.0)

    def test_pick_best_leg_prefers_other_symbol(self):
        legs = [
            LegCandidate("ETHUSDT", "long", 1, 1.0, 100.0, 1.0),
            LegCandidate("BTCUSDT", "long", 2, 1.0, 100.0, 0.2),
        ]
        best = pick_best_leg(legs, "ETHUSDT")
        self.assertEqual(best.symbol, "BTCUSDT")

    def test_pick_best_pair_prefers_other_symbol(self):
        pairs = [
            PairCandidate("ETHUSDT", 0.5),
            PairCandidate("BTCUSDT", 0.1),
        ]
        best = pick_best_pair(pairs, "ETHUSDT")
        self.assertEqual(best.symbol, "BTCUSDT")


class TestEnsureAvailable(unittest.TestCase):
    @patch("src.rsi_trading.close_lot_leg")
    @patch("src.margin_preflight.fetch_futures_balance")
    @patch("src.margin_preflight.MARGIN_PREFLIGHT_ENABLED", True)
    @patch("src.margin_preflight.TRADING_ENABLED", True)
    @patch("src.margin_preflight.has_credentials", return_value=True)
    def test_already_sufficient(self, _creds, mock_balance, mock_close_leg):
        mock_balance.return_value = FuturesAccountBalance(
            margin_coin="USDT",
            available=10.0,
            account_equity=200.0,
            usdt_equity=200.0,
        )
        snap = RsiSnapshot(ready=True, rsi=50.0, close=100.0)
        self.assertTrue(ensure_available_for_pair("SOLUSDT", snap, "test"))
        mock_close_leg.assert_not_called()

    @patch("src.rsi_trading.close_lot_leg")
    @patch("src.margin_preflight._lot_row_by_id")
    @patch("src.margin_preflight.pick_best_leg")
    @patch("src.margin_preflight.collect_leg_candidates")
    @patch("src.margin_preflight.fetch_futures_balance")
    @patch("src.margin_preflight.MARGIN_PREFLIGHT_ENABLED", True)
    @patch("src.margin_preflight.TRADING_ENABLED", True)
    @patch("src.margin_preflight.has_credentials", return_value=True)
    def test_phase_a_closes_leg(
        self,
        _creds,
        mock_balance,
        mock_collect,
        mock_pick,
        mock_close_leg,
        mock_lot_row,
    ):
        mock_collect.return_value = [
            LegCandidate("BTCUSDT", "long", 1, 1.0, 100.0, 0.5),
        ]
        mock_pick.return_value = mock_collect.return_value[0]
        mock_lot_row.return_value = {"id": 1, "long_status": "open", "long_entry": 100.0, "long_size": 1.0}
        mock_balance.side_effect = [
            FuturesAccountBalance("USDT", 1.0, 200.0, 200.0),
            FuturesAccountBalance("USDT", 3.0, 200.0, 200.0),
        ]
        snap = RsiSnapshot(ready=True, rsi=50.0, close=100.0)
        self.assertTrue(ensure_available_for_pair("SOLUSDT", snap, "test"))
        mock_close_leg.assert_called_once()

    @patch("src.rsi_trading.close_hedge_symbol")
    @patch("src.margin_preflight.pick_best_pair")
    @patch("src.margin_preflight.collect_pair_candidates")
    @patch("src.margin_preflight.pick_best_leg", return_value=None)
    @patch("src.margin_preflight.collect_leg_candidates", return_value=[])
    @patch("src.margin_preflight.fetch_futures_balance")
    @patch("src.margin_preflight.MARGIN_PREFLIGHT_ENABLED", True)
    @patch("src.margin_preflight.TRADING_ENABLED", True)
    @patch("src.margin_preflight.has_credentials", return_value=True)
    def test_phase_b_closes_pair(
        self,
        _creds,
        mock_balance,
        _collect_legs,
        _pick_leg,
        mock_collect_pairs,
        mock_pick_pair,
        mock_close_pair,
    ):
        mock_collect_pairs.return_value = [PairCandidate("BTCUSDT", 0.3)]
        mock_pick_pair.return_value = mock_collect_pairs.return_value[0]
        mock_close_pair.return_value = True
        mock_balance.side_effect = [
            FuturesAccountBalance("USDT", 1.0, 200.0, 200.0),
            FuturesAccountBalance("USDT", 5.0, 200.0, 200.0),
        ]
        snap = RsiSnapshot(ready=True, rsi=50.0, close=100.0)
        self.assertTrue(ensure_available_for_pair("SOLUSDT", snap, "test"))
        mock_close_pair.assert_called_once_with("BTCUSDT", unittest.mock.ANY)

    @patch("src.margin_preflight.pick_best_pair", return_value=None)
    @patch("src.margin_preflight.collect_pair_candidates", return_value=[])
    @patch("src.margin_preflight.pick_best_leg", return_value=None)
    @patch("src.margin_preflight.collect_leg_candidates", return_value=[])
    @patch("src.margin_preflight.fetch_futures_balance")
    @patch("src.margin_preflight.MARGIN_PREFLIGHT_ENABLED", True)
    @patch("src.margin_preflight.TRADING_ENABLED", True)
    @patch("src.margin_preflight.has_credentials", return_value=True)
    def test_skip_when_still_insufficient(
        self,
        _creds,
        mock_balance,
        *_mocks,
    ):
        mock_balance.return_value = FuturesAccountBalance("USDT", 0.5, 200.0, 200.0)
        snap = RsiSnapshot(ready=True, rsi=50.0, close=100.0)
        self.assertFalse(ensure_available_for_pair("SOLUSDT", snap, "test"))


class TestOpenPairRollback(unittest.TestCase):
    def test_short_fail_rolls_back_long(self):
        snap = RsiSnapshot(ready=True, rsi=50.0, close=100.0)
        with (
            patch("src.rsi_trading.has_credentials", return_value=True),
            patch("src.rsi_trading.MARGIN_PREFLIGHT_ENABLED", True),
            patch("src.margin_preflight.ensure_available_for_pair", return_value=True),
            patch("src.rsi_trading.is_tradeable_symbol", return_value=True),
            patch("src.rsi_trading.fetch_futures_balance") as mock_balance,
            patch("src.rsi_trading.fetch_side_mark_price", return_value=100.0),
            patch("src.rsi_trading._size_for_margin", return_value="1"),
            patch("src.rsi_trading.place_market_order") as mock_place,
            patch("src.rsi_trading.close_position_side") as mock_close,
            patch("src.rsi_trading._verify_side_reduced") as mock_verify,
            patch("src.rsi_trading._get_state") as mock_state,
            patch("src.rsi_trading._record_market_entry"),
            patch("src.rsi_trading.db.insert_pair_lot"),
        ):
            mock_state.return_value = MagicMock(open_cycle_id=None)
            mock_balance.return_value = MagicMock(account_equity=200.0, available=10.0)
            mock_place.side_effect = [
                {"orderId": "1", "clientOid": "a"},
                ExchangeClientError("insufficient margin"),
            ]
            with self.assertRaises(ExchangeClientError):
                _open_pair("ETHUSDT", snap, "test")
            mock_close.assert_called_once()
            mock_verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
