import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import database as db
from src.web import app as web_app


class TestManualCloseApi(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch("src.database.DATABASE_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()

        self.user_patch = patch.multiple(
            "src.web.app",
            DASHBOARD_USERNAME="jack@example.com",
            DASHBOARD_PASSWORD="secret123",
            DASHBOARD_SESSION_SECRET="test-secret-key-at-least-32-chars-long",
            DASHBOARD_COOKIE_SECURE=False,
        )
        self.user_patch.start()
        self.client = TestClient(web_app.app)
        self.client.post(
            "/login",
            data={"username": "jack@example.com", "password": "secret123"},
        )

    def tearDown(self) -> None:
        self.user_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_close_side_requires_auth(self) -> None:
        anon = TestClient(web_app.app)
        res = anon.post(
            "/api/positions/close-side",
            json={"symbol": "BTCUSDT", "side": "long"},
        )
        self.assertEqual(res.status_code, 401)

    @patch("src.rsi_trading.manual_close_side")
    def test_close_side_ok(self, manual_side) -> None:
        manual_side.return_value = {
            "ok": True,
            "message": "closed BTCUSDT LONG",
            "symbol": "BTCUSDT",
            "side": "long",
            "size": 1.0,
        }
        res = self.client.post(
            "/api/positions/close-side",
            json={"symbol": "BTCUSDT", "side": "long"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        manual_side.assert_called_once_with("BTCUSDT", "long")

    @patch("src.rsi_trading.manual_close_side")
    def test_close_side_rate_limited(self, manual_side) -> None:
        manual_side.return_value = {
            "ok": False,
            "message": "rate-limit cooldown 30s — try later",
            "rate_limited": True,
        }
        res = self.client.post(
            "/api/positions/close-side",
            json={"symbol": "BTCUSDT", "side": "long"},
        )
        self.assertEqual(res.status_code, 429)

    @patch("src.rsi_trading.manual_close_leg")
    def test_close_leg_already_closed(self, manual_leg) -> None:
        manual_leg.return_value = {
            "ok": False,
            "message": "lot #9 long already closed",
        }
        res = self.client.post(
            "/api/positions/close-leg",
            json={"lot_id": 9, "side": "long"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])

    @patch("src.rsi_trading.manual_close_leg")
    def test_close_leg_ok(self, manual_leg) -> None:
        manual_leg.return_value = {
            "ok": True,
            "message": "closed BTCUSDT lot #3 LONG",
            "lot_id": 3,
            "side": "long",
            "fill": 101.0,
            "pnl": 1.0,
            "size": 1.0,
            "entry": 100.0,
            "move_pct": 1.0,
        }
        res = self.client.post(
            "/api/positions/close-leg",
            json={"lot_id": 3, "side": "long"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        manual_leg.assert_called_once_with(3, "long")


class TestComputeOpenLotSideAvg(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch("src.database.DATABASE_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_weighted_avg(self) -> None:
        db.insert_pair_lot(
            "BTCUSDT",
            long_entry=100.0,
            long_size=1.0,
            short_entry=100.0,
            short_size=1.0,
            margin_usdt=1.0,
            entry_trigger="test",
        )
        db.insert_pair_lot(
            "BTCUSDT",
            long_entry=110.0,
            long_size=3.0,
            short_entry=100.0,
            short_size=1.0,
            margin_usdt=1.0,
            entry_trigger="test",
        )
        avg = db.compute_open_lot_side_avg("BTCUSDT", "long")
        self.assertIsNotNone(avg)
        assert avg is not None
        self.assertAlmostEqual(avg[0], (100.0 * 1 + 110.0 * 3) / 4)
        self.assertAlmostEqual(avg[1], 4.0)


if __name__ == "__main__":
    unittest.main()
