import logging
import unittest
from unittest.mock import MagicMock, patch

from src import notify


class TestDiscordNotify(unittest.TestCase):
    def test_notify_error_sends_payload(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        notify._last_error_notify_at.clear()
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_error("Cycle failed", "HTTP 418: IP banned")
            mock_post.assert_called_once()
            content = mock_post.call_args.kwargs["json"]["content"]
            self.assertIn("**Bot lỗi: Cycle failed**", content)
            self.assertIn("HTTP 418: IP banned", content)

    def test_notify_error_cooldown_skips_duplicate(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        notify._last_error_notify_at.clear()
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_error("Cycle failed", "first", cooldown_sec=60)
            notify.notify_error("Cycle failed", "second", cooldown_sec=60)
            self.assertEqual(mock_post.call_count, 1)

    def test_notify_error_different_contexts_both_send(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        notify._last_error_notify_at.clear()
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_error("RSI-rev open LINKUSDT", "timeout")
            notify.notify_error("RSI-rev close LINKUSDT", "reduce-only failed")
            self.assertEqual(mock_post.call_count, 2)

    def test_error_log_handler_sends_discord(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        notify._last_error_notify_at.clear()
        handler = notify.DiscordErrorLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="src.rsi_rev.cycle",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="boom",
            args=(),
            exc_info=None,
        )
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            handler.emit(record)
            mock_post.assert_called_once()
            self.assertIn("boom", mock_post.call_args.kwargs["json"]["content"])

    def test_error_log_handler_respects_skip_discord(self) -> None:
        handler = notify.DiscordErrorLogHandler()
        record = logging.LogRecord(
            name="src.rsi_rev.cycle",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="already notified",
            args=(),
            exc_info=None,
        )
        record.skip_discord = True
        with patch.object(notify, "notify_error") as mock_notify:
            handler.emit(record)
            mock_notify.assert_not_called()
        notify._last_error_notify_at.clear()
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", ""),
            patch.object(notify, "_send_discord") as mock_send,
        ):
            notify.notify_error("Cycle failed", "boom")
            mock_send.assert_not_called()

    def test_rsi_rev_open_includes_anchor_and_tp(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_rsi_rev_open(
                "LINKUSDT",
                "long",
                zone="RSI >= 70",
                anchor=10.0,
                entry=9.95,
                tp=9.975,
                size=10,
                margin_usdt=12.34,
            )
            content = mock_post.call_args.kwargs["json"]["content"]
            self.assertIn("LINKUSDT LONG mở", content)
            self.assertIn("anchor=10", content)
            self.assertIn("target TP=", content)

    def test_rsi_rev_close_includes_reason(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_rsi_rev_close(
                "LINKUSDT",
                "short",
                reason="TP về vùng RSI",
                zone="RSI >= 70",
                anchor=10.0,
                entry=10.05,
                tp=10.025,
                close_price=10.025,
                pnl_usdt=1.25,
                opened_at="2026-08-01T00:00:00+00:00",
            )
            content = mock_post.call_args.kwargs["json"]["content"]
            self.assertIn("TP về vùng RSI", content)
            self.assertIn("pnl=+1.25 USDT", content)

    def test_rsi_rev_open_skips_without_webhook(self) -> None:
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", ""),
            patch.object(notify, "_send_discord") as mock_send,
        ):
            notify.notify_rsi_rev_open(
                "LINKUSDT",
                "long",
                zone="RSI <= 30",
                anchor=1,
                entry=0.995,
                tp=0.9975,
                size=1,
                margin_usdt=1,
            )
            mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
