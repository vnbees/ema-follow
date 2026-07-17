import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import database as db
from src.web import app as web_app


class TestPushSubscribe(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch("src.database.DATABASE_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()

        self.cfg = patch.multiple(
            "src.web.app",
            DASHBOARD_USERNAME="jack@example.com",
            DASHBOARD_PASSWORD="secret123",
            DASHBOARD_SESSION_SECRET="test-secret-key-at-least-32-chars-long",
            DASHBOARD_COOKIE_SECURE=False,
            VAPID_PUBLIC_KEY="BFtestPublicKeyForUnitTestsXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        )
        self.cfg.start()
        self.vapid = patch("src.web.app.vapid_configured", return_value=True)
        self.vapid.start()
        self.client = TestClient(web_app.app)
        login = self.client.post(
            "/login",
            data={"username": "jack@example.com", "password": "secret123"},
            follow_redirects=False,
        )
        self.assertIn(login.status_code, (303, 302))

    def tearDown(self) -> None:
        self.vapid.stop()
        self.cfg.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_subscribe_and_list(self) -> None:
        res = self.client.post(
            "/api/push/subscribe",
            json={
                "endpoint": "https://example.com/push/abc",
                "keys": {"p256dh": "p256", "auth": "authk"},
            },
        )
        self.assertEqual(res.status_code, 200)
        rows = db.list_push_subscriptions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["endpoint"], "https://example.com/push/abc")

    def test_manifest_and_sw_public(self) -> None:
        anon = TestClient(web_app.app)
        m = anon.get("/manifest.webmanifest")
        self.assertEqual(m.status_code, 200)
        sw = anon.get("/sw.js")
        self.assertEqual(sw.status_code, 200)


if __name__ == "__main__":
    unittest.main()
