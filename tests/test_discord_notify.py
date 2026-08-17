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
            notify.notify_error("EMA-RSI open BTCUSDT", "timeout")
            notify.notify_error("EMA-RSI SL/TP BTCUSDT", "algo failed")
            self.assertEqual(mock_post.call_count, 2)

    def test_notify_error_skip_when_url_missing(self) -> None:
        notify._last_error_notify_at.clear()
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", ""),
            patch.object(notify, "_send_discord") as mock_send,
        ):
            notify.notify_error("Cycle failed", "boom")
            mock_send.assert_not_called()

    def test_ema_rsi_open_includes_entry_sl_tp(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_ema_rsi_open(
                "BTCUSDT",
                "long",
                entry=100.0,
                sl=90.0,
                tp=120.0,
                r=10.0,
                rr=2,
                margin_usdt=12.34,
            )
            content = mock_post.call_args.kwargs["json"]["content"]
            self.assertIn("BTCUSDT LONG mở", content)
            self.assertIn("entry=100", content)
            self.assertIn("SL=90", content)
            self.assertIn("TP=120", content)

    def test_ema_rsi_close_distinguishes_sl_and_tp(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_ema_rsi_close(
                "BTCUSDT",
                "short",
                reason="HIT_SL",
                entry=100.0,
                sl=110.0,
                tp=80.0,
                close_price=110.0,
                pnl_usdt=-5.5,
            )
            content = mock_post.call_args.kwargs["json"]["content"]
            self.assertIn("HIT SL", content)
            self.assertIn("pnl=-5.50 USDT", content)

    def test_ema_rsi_open_skips_without_webhook(self) -> None:
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", ""),
            patch.object(notify, "_send_discord") as mock_send,
        ):
            notify.notify_ema_rsi_open(
                "ETHUSDT", "long", entry=1, sl=0.5, tp=2, r=0.5, rr=2, margin_usdt=1
            )
            mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
