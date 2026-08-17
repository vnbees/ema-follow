import unittest
from unittest.mock import MagicMock, patch

from src import notify


class TestDiscordNotify(unittest.TestCase):
    def test_skip_when_url_missing(self) -> None:
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", ""),
            patch.object(notify, "_send_discord") as mock_send,
        ):
            notify.notify_close("AAVEUSDT", "LONG")
            mock_send.assert_not_called()

    def test_sends_discord_payload(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch.object(
                notify,
                "_format_balance_body",
                return_value=(
                    "Futures balance: available=1.00 USDT | equity=2.00 USDT"
                    " | maint=1.00% | initial=2.00%"
                ),
            ),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_close("AAVEUSDT", "LONG")
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertEqual(args[0], "https://discord.com/api/webhooks/test/token")
            content = kwargs["json"]["content"]
            self.assertIn("**AAVEUSDT đóng LONG**", content)
            self.assertIn("Futures balance:", content)

    def test_title_includes_close_reason(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch.object(notify, "_format_balance_body", return_value="balance"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_close("ETHUSDT", "SHORT×1", reason="tp")
            content = mock_post.call_args.kwargs["json"]["content"]
            self.assertIn("**ETHUSDT đóng SHORT×1 — chạm TP", content)

    def test_title_uses_reason_text_for_mixed_batch(self) -> None:
        mock_resp = MagicMock(ok=True, status_code=204, text="")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch.object(notify, "_format_balance_body", return_value="balance"),
            patch("src.notify.requests.post", return_value=mock_resp) as mock_post,
        ):
            notify.notify_close(
                "BTCUSDT",
                "LONG×3",
                reason_text="chạm TP 0.5% ×2; chạm entry (chân còn lại đã TP) ×1",
            )
            content = mock_post.call_args.kwargs["json"]["content"]
            self.assertIn("**BTCUSDT đóng LONG×3 — chạm TP 0.5% ×2; chạm entry", content)

    def test_http_error_does_not_raise(self) -> None:
        mock_resp = MagicMock(ok=False, status_code=500, text="err")
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"),
            patch.object(notify, "_format_balance_body", return_value="balance"),
            patch("src.notify.requests.post", return_value=mock_resp),
        ):
            notify.notify_close("ETHUSDT", "SHORT")

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

    def test_notify_error_skip_when_url_missing(self) -> None:
        notify._last_error_notify_at.clear()
        with (
            patch.object(notify, "DISCORD_WEBHOOK_URL", ""),
            patch.object(notify, "_send_discord") as mock_send,
        ):
            notify.notify_error("Cycle failed", "boom")
            mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
