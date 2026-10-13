import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import database as db
from src.web import app as web_app


class TestDashboardAuth(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.user_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_root_redirects_to_login_when_anonymous(self) -> None:
        res = self.client.get("/", follow_redirects=False)
        self.assertIn(res.status_code, (302, 303))
        self.assertEqual(res.headers.get("location"), "/login")

    def test_api_unauthorized_when_anonymous(self) -> None:
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 401)

    def test_login_success_then_dashboard(self) -> None:
        res = self.client.post(
            "/login",
            data={"username": "jack@example.com", "password": "secret123"},
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (302, 303))
        self.assertEqual(res.headers.get("location"), "/")

        home = self.client.get("/", follow_redirects=False)
        self.assertEqual(home.status_code, 200)
        self.assertIn("SMC quét thanh khoản".encode(), home.content)
        self.assertIn("Winrate".encode(), home.content)

        status = self.client.get("/api/smc/status")
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertIn("summary", body)
        self.assertIn("coins", body)
        self.assertEqual(body["summary"]["closed_n"], 0)

        donchian = self.client.get("/donchian", follow_redirects=False)
        self.assertEqual(donchian.status_code, 200)
        self.assertIn("Donchian 2 chiều".encode(), donchian.content)

        legacy = self.client.get("/legacy", follow_redirects=False)
        self.assertEqual(legacy.status_code, 200)

    def test_smc_summary_counts_closed_trades(self) -> None:
        from src.smc import store as smc_store

        smc_store.ensure_schema()
        win = smc_store.insert_pending(
            symbol="UNIUSDT", side="long", signal_ts=1, entry=1, stop=0.9, target=1.2, margin=10
        )
        smc_store.mark_open(win, fill_px=1, qty=10, fee_open=0.01)
        smc_store.mark_closed(win, close_px=1.2, fee_close=0.01, pnl=1.5, reason="tp")
        loss = smc_store.insert_pending(
            symbol="BTCUSDT", side="short", signal_ts=2, entry=2, stop=2.1, target=1.5, margin=10
        )
        smc_store.mark_open(loss, fill_px=2, qty=5, fee_open=0.01)
        smc_store.mark_closed(loss, close_px=2.1, fee_close=0.02, pnl=-0.5, reason="stop")

        self.client.post(
            "/login",
            data={"username": "jack@example.com", "password": "secret123"},
            follow_redirects=False,
        )
        body = self.client.get("/api/smc/status").json()
        self.assertEqual(body["summary"]["closed_n"], 2)
        self.assertEqual(body["summary"]["wins"], 1)
        self.assertAlmostEqual(body["summary"]["win_rate"], 50.0)
        self.assertAlmostEqual(body["summary"]["realized"], 1.0)
        self.assertAlmostEqual(body["summary"]["pf"], 3.0)
        coins = {row["symbol"]: row for row in body["coins"]}
        self.assertEqual(coins["UNIUSDT"]["closed_n"], 1)
        self.assertEqual(coins["BTCUSDT"]["wins"], 0)

    def test_login_wrong_password(self) -> None:
        res = self.client.post(
            "/login",
            data={"username": "jack@example.com", "password": "wrong"},
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn(b"kh\xc3\xb4ng \xc4\x91\xc3\xbang", res.content)


if __name__ == "__main__":
    unittest.main()
