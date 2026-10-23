import unittest

import numpy as np

from src.predict5m.api import (
    is_5m_updown_topic,
    is_updown_topic,
    market_is_open,
    parse_available_usdt,
    pick_outcome,
    usdt_to_wei,
)
from src.predict5m.config import TIMEFRAMES, interval_ms, place_grace_ms
from src.predict5m.broker import place_body, quote_body
from src.predict5m.candles import TV_DIRECTIONAL, latch_labels, latest_labels
from src.predict5m.engine import (
    Bankroll,
    after_recovery,
    bankroll_from_loss,
    after_result,
    cannot_afford,
    last_valid_closed,
    opposite_body,
    order_is_filled,
    pick_first,
    qualifies,
    resolve_window,
    settle_pnl,
    stake_from_target,
    stake_usdt,
    take_closed,
    target_limit_price,
    window_fresh,
    Signal,
)
from src.predict5m.sqz import _stdev, hist_matches_label, squeeze


class QualifiesTests(unittest.TestCase):
    def test_all_conditions_required(self):
        self.assertTrue(
            qualifies(label=1, hist="lime", dot="gray", open_px=100, close_px=99)
        )
        self.assertTrue(
            qualifies(label=-1, hist="red", dot="gray", open_px=100, close_px=101)
        )
        self.assertFalse(
            qualifies(label=0, hist="lime", dot="gray", open_px=100, close_px=99)
        )
        self.assertFalse(
            qualifies(label=1, hist="lime", dot="black", open_px=100, close_px=99)
        )
        self.assertFalse(
            qualifies(label=1, hist="lime", dot="blue", open_px=100, close_px=99)
        )
        self.assertFalse(
            qualifies(label=1, hist="red", dot="gray", open_px=100, close_px=99)
        )
        self.assertFalse(
            qualifies(label=1, hist="lime", dot="gray", open_px=100, close_px=101)
        )
        self.assertFalse(
            qualifies(label=-1, hist="maroon", dot="gray", open_px=100, close_px=99)
        )

    def test_hist_family(self):
        self.assertTrue(hist_matches_label(1, "green"))
        self.assertTrue(hist_matches_label(-1, "maroon"))
        self.assertFalse(hist_matches_label(1, "maroon"))

    def test_opposite_body(self):
        self.assertTrue(opposite_body(1, 10, 9))
        self.assertTrue(opposite_body(-1, 10, 11))
        self.assertFalse(opposite_body(1, 10, 10))


class MartingaleTests(unittest.TestCase):
    def test_stakes(self):
        self.assertEqual(stake_usdt(0, base=1), 1)
        self.assertEqual(stake_usdt(1, base=1), 2)
        self.assertEqual(stake_usdt(2, base=1), 4)
        self.assertEqual(stake_usdt(3, base=1), 8)
        self.assertEqual(stake_usdt(5, base=1), 32)

    def test_win_resets(self):
        self.assertEqual(after_result(True, 3), 0)

    def test_unfilled_does_not_double(self):
        self.assertEqual(after_result(False, 3, filled=False), 3)
        self.assertEqual(after_result(True, 3, filled=False), 3)

    def test_limit_price_for_double_profit(self):
        px = target_limit_price(200, profit_mult=2, decimals=2)
        self.assertEqual(px, 0.32)
        self.assertGreaterEqual(settle_pnl(True, 1, limit_px=px, fee_bps=200), 2.0)
        self.assertEqual(settle_pnl(True, 1, filled=False), 0.0)
        self.assertTrue(order_is_filled("FILLED"))
        self.assertTrue(order_is_filled("PARTIALLY_FILLED"))
        self.assertFalse(order_is_filled("SUBMITTED"))
        self.assertFalse(order_is_filled("CANCELLED"))

    def test_doubles_until_wallet_breaks(self):
        step = 0
        stakes = []
        wallet = 59.67
        spent = 0.0
        while True:
            stake = stake_usdt(step, base=1)
            if cannot_afford(stake, wallet - spent):
                break
            stakes.append(stake)
            spent += stake
            step = after_result(False, step)
        self.assertEqual(stakes, [1, 2, 4, 8, 16])
        self.assertEqual(step, 5)
        self.assertEqual(stake_usdt(step, base=1), 32)
        self.assertTrue(cannot_afford(32, wallet - spent))

    def test_recovery_target_sizes_stake_at_2x(self):
        self.assertEqual(stake_from_target(1, profit_mult=2), 0.5)
        bank = Bankroll(target=1, streak_loss=0)
        lost = after_recovery(False, bank, filled=True, stake=0.5, base=1)
        self.assertAlmostEqual(lost.streak_loss, 0.5)
        self.assertAlmostEqual(lost.target, 1.5)  # hole 0.5 + base 1
        self.assertEqual(stake_from_target(lost.target, profit_mult=2), 0.75)
        same = after_recovery(False, lost, filled=False, stake=0.75, base=1)
        self.assertEqual(same, lost)
        reset = after_recovery(True, lost, filled=True, stake=0.75, base=1)
        self.assertEqual(reset.target, 1)
        self.assertEqual(reset.streak_loss, 0)

    def test_recovery_is_hole_plus_base(self):
        self.assertEqual(bankroll_from_loss(0, 2), Bankroll(target=2, streak_loss=0))
        bank = Bankroll(target=2, streak_loss=0)
        nxt = after_recovery(False, bank, filled=True, stake=1, base=2)
        self.assertAlmostEqual(nxt.streak_loss, 1)
        self.assertAlmostEqual(nxt.target, 3)
        self.assertEqual(stake_from_target(nxt.target, profit_mult=2), 1.5)
        nxt2 = after_recovery(False, nxt, filled=True, stake=1.5, base=2)
        self.assertAlmostEqual(nxt2.streak_loss, 2.5)
        self.assertAlmostEqual(nxt2.target, 4.5)
        self.assertEqual(stake_from_target(nxt2.target, profit_mult=2), 2.25)

    def test_parse_cedefi_balance(self):
        items = [
            {"accountType": "CeDeFi", "availableBalanceDisplay": "59.67", "enabled": True},
            {"accountType": "SPOT", "availableBalanceDisplay": "0.00", "enabled": True},
            {"accountType": "FUNDING", "availableBalanceDisplay": "0.00", "enabled": True},
        ]
        self.assertEqual(parse_available_usdt(items), 59.67)
        self.assertFalse(cannot_afford(32, 59.67))
        self.assertTrue(cannot_afford(64, 59.67))


class PickTests(unittest.TestCase):
    def test_first_symbol_wins(self):
        btc = Signal("BTCUSDT", 1, 1, 2, 300001, 1, "lime", "gray", 1, 0.9)
        eth = Signal("ETHUSDT", -1, 1, 2, 300001, -1, "red", "gray", 1, 1.1)
        self.assertEqual(pick_first([None, None, eth]).symbol, "ETHUSDT")
        self.assertEqual(pick_first([btc, eth]).symbol, "BTCUSDT")
        self.assertIsNone(pick_first([None, None]))


class PlaceHelpersTests(unittest.TestCase):
    def test_one_usdt_is_wei(self):
        self.assertEqual(usdt_to_wei(1), "1000000000000000000")
        self.assertEqual(usdt_to_wei(1.0), "1000000000000000000")

    def test_slug_5m_not_15m(self):
        eth5 = {"slug": "eth-updown-5m-1792586100", "symbol": "ETHUSDT"}
        eth15 = {"slug": "eth-updown-15m-1792585800", "symbol": "ETHUSDT"}
        eth1h = {"slug": "eth-updown-1h-1792584000", "symbol": "ETHUSDT"}
        self.assertTrue(is_5m_updown_topic(eth5, "ETHUSDT"))
        self.assertFalse(is_5m_updown_topic(eth15, "ETHUSDT"))
        self.assertFalse(is_5m_updown_topic(eth5, "BTCUSDT"))
        self.assertTrue(is_updown_topic(eth15, "ETHUSDT", "15m"))
        self.assertTrue(is_updown_topic(eth1h, "ETHUSDT", "1h"))
        self.assertFalse(is_updown_topic(eth15, "ETHUSDT", "5m"))
        self.assertFalse(is_updown_topic(eth15, "ETHUSDT", "1h"))
        self.assertFalse(is_updown_topic(eth1h, "ETHUSDT", "15m"))

    def test_pick_up_from_nested_outcomes(self):
        topic = {
            "markets": [
                {
                    "tradingStatus": "OPEN",
                    "outcomes": [
                        {"name": "Up", "tokenId": "aaa"},
                        {"name": "Down", "tokenId": "bbb"},
                    ]
                }
            ]
        }
        self.assertEqual(pick_outcome(topic, "UP")["tokenId"], "aaa")
        self.assertEqual(pick_outcome(topic, "DOWN")["tokenId"], "bbb")
        self.assertTrue(market_is_open(topic))
        self.assertFalse(market_is_open({"markets": [{"tradingStatus": "CLOSED"}]}))

    def test_quote_and_place_are_limit(self):
        q = quote_body(
            wallet_address="0xabc",
            token_id="tok",
            amount_wei="1000000000000000000",
            slippage_bps=1000,
            chain_id="56",
            price_limit="0.32",
        )
        self.assertEqual(q["orderType"], "LIMIT")
        self.assertEqual(q["priceLimit"], "0.32")
        self.assertEqual(q["amountIn"], "1000000000000000000")
        p = place_body(
            quote_id="qid",
            slippage_bps=1000,
            wallet_address="0xabc",
            wallet_id="wid",
            price_limit="0.32",
        )
        self.assertEqual(p["orderType"], "LIMIT")
        self.assertEqual(p["timeInForce"], "GTC")
        self.assertEqual(p["priceLimit"], "0.32")
        self.assertNotEqual(p["timeInForce"], "FOK")
        self.assertEqual(p["accountType"], "SPOT")
        self.assertEqual(p["walletId"], "wid")
        self.assertEqual(p["quoteId"], "qid")

    def test_forming_bar_never_evaluated(self):
        ts = np.array([0, 300_000, 600_000], dtype=np.int64)
        px = np.array([1.0, 2.0, 3.0])
        close_ts = np.array([299_999, 599_999, 899_999], dtype=np.int64)
        now = 700_000  # third bar still running
        got = take_closed(ts, px, px, px, px, close_ts, now)
        self.assertEqual(list(got[0]), [0, 300_000])
        self.assertEqual(list(got[5]), [299_999, 599_999])
        n = 90
        ts = np.arange(n, dtype=np.int64) * 300_000
        close_ts = ts + 299_999
        o = np.full(n, 100.0)
        now_ms = int(close_ts[-1])  # last bar not yet closed
        ts2, *_rest, close2 = take_closed(ts, o, o + 1, o - 1, o, close_ts, now_ms)
        self.assertEqual(len(ts2), n - 1)
        self.assertEqual(int(ts2[-1]), int(ts[-2]))
        self.assertIsNone(last_valid_closed("BNBUSDT", ts, o, o + 1, o - 1, o, close_ts, 0))

    def test_window_fresh_only_at_open(self):
        start = 1_000_000
        self.assertTrue(window_fresh(start, start, grace_ms=20_000))
        self.assertTrue(window_fresh(start, start + 15_000, grace_ms=20_000))
        self.assertFalse(window_fresh(start, start + 120_000, grace_ms=20_000))
        self.assertFalse(window_fresh(start, start - 1, grace_ms=20_000))

    def test_resolve_window(self):
        self.assertEqual(resolve_window("UP", 100, 101), (True, "up_win"))
        self.assertEqual(resolve_window("UP", 100, 99), (False, "up_lose"))
        self.assertEqual(resolve_window("DOWN", 100, 99), (True, "down_win"))
        self.assertEqual(resolve_window("DOWN", 100, 100), (False, "tie"))
        self.assertEqual(settle_pnl(True, 1), 0.98)
        self.assertEqual(settle_pnl(False, 1), -1.0)


class LatchTests(unittest.TestCase):
    def test_gray_clears_directional(self):
        raw = np.array([0, -1, 0, 0, 1, 0], dtype=np.int8)
        gray = np.array([0, 0, 1, 0, 0, 0], dtype=bool)
        got = list(latch_labels(raw, gray))
        self.assertEqual(got, [0, -1, 0, 0, 1, 1])

    def test_any_non_blue_red_on_same_bar_invalidates(self):
        raw = np.array([-1], dtype=np.int8)
        gray = np.array([True])
        self.assertEqual(int(latch_labels(raw, gray)[0]), 0)

    def test_latch_uses_tv_default_patterns_only(self):
        self.assertEqual(
            TV_DIRECTIONAL,
            ("AbandonedBaby", "DragonflyDoji", "Engulfing", "Hammer"),
        )


class TimeframeTests(unittest.TestCase):
    def test_interval_ms_and_grace(self):
        self.assertIn("5m", TIMEFRAMES)
        self.assertIn("15m", TIMEFRAMES)
        self.assertIn("1h", TIMEFRAMES)
        self.assertEqual(interval_ms("5m"), 5 * 60 * 1000)
        self.assertEqual(interval_ms("15m"), 15 * 60 * 1000)
        self.assertEqual(interval_ms("1h"), 60 * 60 * 1000)
        self.assertGreater(place_grace_ms("15m"), place_grace_ms("5m"))
        self.assertGreater(place_grace_ms("1h"), place_grace_ms("5m"))

    def test_signal_keeps_interval_and_15m_entry(self):
        from src.predict5m.engine import Signal

        sig = Signal(
            "BNBUSDT", 1, 0, 899_999, 900_000, 1, "lime", "gray", 1, 0.9, "15m"
        )
        self.assertEqual(sig.interval, "15m")
        self.assertEqual(sig.entry_ts, 900_000)

    def test_shared_martingale_and_one_open_bet(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from src.predict5m import store as pstore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "predict.db"
            with patch("src.database.DATABASE_PATH", path):
                pstore.ensure_schema()
                pstore.set_bankroll(Bankroll(target=2.5, streak_loss=0.5))
                got = pstore.bankroll()
                self.assertAlmostEqual(got.target, 2.5)
                self.assertAlmostEqual(got.streak_loss, 0.5)
                pstore.insert_open(
                    symbol="BNBUSDT",
                    side="DOWN",
                    stake=4,
                    double_step=2,
                    bar_ts=1_000,
                    entry_ts=1_300_000,
                    market_topic_id=1,
                    timeframe="5m",
                )
                self.assertEqual(pstore.open_bet()["timeframe"], "5m")
                self.assertEqual(len(pstore.open_bets()), 1)
                until = pstore.start_pause(0, 3_600_000)
                self.assertTrue(pstore.is_paused(1_000))
                self.assertEqual(until, 3_600_000)
                reset = pstore.bankroll()
                from src.predict5m.config import TARGET_USDT

                self.assertEqual(reset.target, TARGET_USDT)
                self.assertEqual(reset.streak_loss, 0)

    def test_scan_all_prefers_smaller_timeframe(self):
        from unittest.mock import patch

        from src.predict5m import runtime

        calls: list[str] = []

        def fake_scan(now_ms=None, *, timeframe="5m"):
            calls.append(timeframe)
            if timeframe == "5m":
                return {"skipped": "no valid signal", "timeframe": "5m"}
            if timeframe == "15m":
                return {"opened": {"id": 1}, "timeframe": "15m"}
            return {"skipped": "should not run", "timeframe": timeframe}

        with patch.object(runtime, "scan_once", side_effect=fake_scan):
            out = runtime.scan_all(now_ms=1)
        self.assertEqual(calls, ["5m", "15m"])
        self.assertEqual(out[-1]["timeframe"], "15m")


class SqueezeSmokeTests(unittest.TestCase):
    def test_pine_stdev_is_biased(self):
        xs = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        # population /n = 2; sample /(n-1) ≈ 2.138
        self.assertAlmostEqual(_stdev(xs, len(xs) - 1, len(xs)), 2.0)

    def test_length(self):
        n = 80
        closes = [100.0 + (i % 7) * 0.1 for i in range(n)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        hist, dot, val = squeeze(highs, lows, closes)
        self.assertEqual(len(hist), n)
        self.assertEqual(len(dot), n)
        self.assertTrue(any(x in ("gray", "black", "blue") for x in dot if x))


if __name__ == "__main__":
    unittest.main()
