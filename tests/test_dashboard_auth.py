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
        self.assertIn(b"RSI Bot", home.content)

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
