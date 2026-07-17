import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import database as db
from src.rsi import RsiSnapshot
from src.rsi_trading import _open_pair, evaluate_rsi_trade
from src.trading import _states, ensure_symbol_configured, on_symbol_added, reset_symbol_state


class TestEnsureSymbolConfiguredDb(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch("src.database.DATABASE_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()
        _states.pop("ETHUSDT", None)
        reset_symbol_state("ETHUSDT")

    def tearDown(self) -> None:
        reset_symbol_state("ETHUSDT")
        self.db_patch.stop()
        self.tmp.cleanup()

    @patch("src.trading.configure_symbol_trading")
    def test_db_match_skips_api(self, mock_configure) -> None:
        db.upsert_symbol_trading_config("ETHUSDT", "crossed", 10)
        ensure_symbol_configured("ETHUSDT")
        mock_configure.assert_not_called()

    @patch("src.trading.configure_symbol_trading")
    def test_db_mismatch_calls_api(self, mock_configure) -> None:
        db.upsert_symbol_trading_config("ETHUSDT", "crossed", 5)
        ensure_symbol_configured("ETHUSDT")
        mock_configure.assert_called_once_with("ETHUSDT")
        row = db.get_symbol_trading_config("ETHUSDT")
        assert row is not None
        self.assertEqual(int(row["leverage"]), 10)

    @patch("src.trading.configure_symbol_trading")
    def test_ram_cache_after_first_call(self, mock_configure) -> None:
        db.upsert_symbol_trading_config("ETHUSDT", "crossed", 10)
        ensure_symbol_configured("ETHUSDT")
        ensure_symbol_configured("ETHUSDT")
        mock_configure.assert_not_called()


class TestLazyOpenOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch("src.database.DATABASE_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()
        _states.pop("ETHUSDT", None)
        reset_symbol_state("ETHUSDT")

    def tearDown(self) -> None:
        reset_symbol_state("ETHUSDT")
        self.db_patch.stop()
        self.tmp.cleanup()

    @patch("src.rsi_trading.ensure_symbol_configured")
    @patch("src.rsi_trading._scan_take_profits", return_value=False)
    @patch("src.rsi_trading._update_status")
    @patch("src.rsi_trading._sync_lots_with_exchange")
    @patch("src.rsi_trading.fetch_side_mark_price", return_value=100.0)
    @patch("src.rsi_trading.has_credentials", return_value=True)
    @patch("src.rsi_trading._trading_enabled", return_value=True)
    @patch("src.rsi_trading.is_tradeable_symbol", return_value=True)
    def test_evaluate_tp_cycle_skips_ensure(
        self,
        _tradeable,
        _enabled,
        _creds,
        _mark,
        _sync,
        _status,
        _scan,
        mock_ensure,
    ) -> None:
        snap = RsiSnapshot(ready=True, rsi=50.0, close=100.0)
        evaluate_rsi_trade("ETHUSDT", snap, signal=None)
        mock_ensure.assert_not_called()

    @patch("src.rsi_trading.MARGIN_PREFLIGHT_ENABLED", False)
    @patch("src.rsi_trading.is_tradeable_symbol", return_value=True)
    @patch("src.rsi_trading.ensure_symbol_configured")
    def test_open_pair_calls_ensure(self, mock_ensure, *_mocks) -> None:
        mock_ensure.side_effect = RuntimeError("stop-after-ensure")
        snap = RsiSnapshot(ready=True, rsi=50.0, close=100.0)
        with self.assertRaises(RuntimeError):
            _open_pair("ETHUSDT", snap, "test")
        mock_ensure.assert_called_once_with("ETHUSDT")


class TestOnSymbolAddedLazy(unittest.TestCase):
    @patch("src.trading.ensure_symbol_configured")
    def test_on_symbol_added_does_not_configure(self, mock_ensure) -> None:
        on_symbol_added("ETHUSDT")
        mock_ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
