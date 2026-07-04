import unittest
from unittest.mock import MagicMock, patch

from src.config import (
    MARGIN_MAINT_HIGH_PCT,
    MARGIN_MAINT_WARN_PCT,
    PAIR_PROFIT_TARGET_PCT,
)
from src.exchange.types import FuturesAccountBalance
from src.margin_guard import (
    MarginStats,
    _tier_for_pct,
    effective_tp_pct,
    process_margin_guard_cycle,
    should_block_new_entries,
    suggest_deposit_usdt,
)
from src.rsi_trading import _rank_symbols_for_deleverage


class TestFuturesAccountBalanceMargin(unittest.TestCase):
    def test_maint_margin_pct(self):
        balance = FuturesAccountBalance(
            margin_coin="USDT",
            available=40.0,
            account_equity=200.0,
            usdt_equity=200.0,
            total_maint_margin=30.0,
            total_initial_margin=150.0,
        )
        self.assertAlmostEqual(balance.maint_margin_pct, 15.0)
        self.assertAlmostEqual(balance.initial_margin_pct, 75.0)


class TestMarginGuardTiers(unittest.TestCase):
    def tearDown(self):
        from src.margin_guard import reset_margin_guard_state
        reset_margin_guard_state()

    def test_tier_ok_watch_elevated(self):
        self.assertEqual(_tier_for_pct(10.0), "ok")
        self.assertEqual(_tier_for_pct(18.0), "watch")
        self.assertEqual(_tier_for_pct(21.0), "elevated")
        self.assertEqual(_tier_for_pct(26.0), "high")
        self.assertEqual(_tier_for_pct(40.0), "critical")

    def test_low_available_alone_not_critical(self):
        self.assertEqual(_tier_for_pct(10.0), "ok")

    def test_suggest_deposit(self):
        deposit = suggest_deposit_usdt(40.0, 178.0, target_pct=18.0)
        self.assertIsNotNone(deposit)
        self.assertAlmostEqual(deposit, 44.22, places=1)

    @patch("src.margin_guard.fetch_account_margin_stats")
    @patch("src.margin_guard.TRADING_ENABLED", True)
    @patch("src.margin_guard.MARGIN_GUARD_ENABLED", True)
    @patch("src.margin_guard.has_credentials", return_value=True)
    def test_elevated_blocks_entries(self, _creds, mock_stats):
        mock_stats.return_value = MarginStats(
            equity=200.0,
            available=50.0,
            maint_margin=42.0,
            initial_margin=120.0,
            maint_margin_pct=21.0,
            initial_margin_pct=60.0,
        )
        state = process_margin_guard_cycle("BTCUSDT")
        self.assertEqual(state.tier, "elevated")
        self.assertTrue(should_block_new_entries())
        self.assertAlmostEqual(effective_tp_pct(), PAIR_PROFIT_TARGET_PCT)

    @patch("src.margin_guard.fetch_account_margin_stats")
    @patch("src.margin_guard.TRADING_ENABLED", True)
    @patch("src.margin_guard.MARGIN_GUARD_ENABLED", True)
    @patch("src.margin_guard.has_credentials", return_value=True)
    def test_high_uses_lower_tp(self, _creds, mock_stats):
        import src.margin_guard as mg

        mg._guard_state.elevated_cycles = 0
        mg._guard_state.high_cycles = 0
        mg._elevated_since_pct = None
        mock_stats.return_value = MarginStats(
            equity=200.0,
            available=30.0,
            maint_margin=52.0,
            initial_margin=140.0,
            maint_margin_pct=MARGIN_MAINT_HIGH_PCT + 1,
            initial_margin_pct=70.0,
        )
        state = process_margin_guard_cycle("BTCUSDT")
        self.assertEqual(state.tier, "high")
        self.assertTrue(should_block_new_entries())
        self.assertAlmostEqual(effective_tp_pct(), 1.0)


class TestDeleverageRank(unittest.TestCase):
    @patch("src.rsi_trading.fetch_side_unrealized_pnl", return_value=0.0)
    @patch("src.rsi_trading.fetch_symbol_positions")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    def test_prefers_more_stacked_lots(self, mock_lots, mock_positions, _pnl):
        def lots_for(symbol):
            if symbol == "AAAUSDT":
                return [
                    {"long_status": "open", "short_status": "open", "opened_at": "2024-01-01"},
                    {"long_status": "open", "short_status": "open", "opened_at": "2024-01-02"},
                ]
            return [
                {"long_status": "open", "short_status": "open", "opened_at": "2024-01-03"},
            ]

        mock_lots.side_effect = lots_for

        def pos_for(symbol):
            p = MagicMock()
            p.size = 1.0
            return {"long": p, "short": p}

        mock_positions.side_effect = pos_for

        ranked = _rank_symbols_for_deleverage(["BBBUSDT", "AAAUSDT"])
        self.assertEqual(ranked[0], "AAAUSDT")


if __name__ == "__main__":
    unittest.main()
