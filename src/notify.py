"""Discord webhook notifications (close + errors). Fail-soft — never breaks trading."""

from __future__ import annotations

import logging
import threading
import time

import requests

from src.config import (
    DEFAULT_SYMBOL,
    DISCORD_WEBHOOK_URL,
    MARGIN_COIN,
)
from src.exchange import ExchangeClientError, fetch_futures_balance, has_credentials

# Same error context: at most one Discord ping per cooldown window.
_ERROR_NOTIFY_COOLDOWN_SEC = 180.0
_last_error_notify_at: dict[str, float] = {}


def discord_configured() -> bool:
    return bool(DISCORD_WEBHOOK_URL)


def _format_balance_body() -> str:
    if not has_credentials():
        return f"Futures balance: unavailable ({MARGIN_COIN})"
    try:
        balance = fetch_futures_balance(DEFAULT_SYMBOL)
        return (
            f"Futures balance: available={balance.available:.2f} {balance.margin_coin}"
            f" | equity={balance.account_equity:.2f} {balance.margin_coin}"
            f" | maint={balance.maint_margin_pct:.2f}%"
            f" | initial={balance.initial_margin_pct:.2f}%"
        )
    except ExchangeClientError as exc:
        logging.warning("Discord notify: balance fetch failed (%s)", exc)
        return f"Futures balance: fetch failed ({MARGIN_COIN})"


def _send_discord(title: str, body: str) -> None:
    content = f"**{title}**\n{body}"
    if len(content) > 2000:
        content = content[:1997] + "..."
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": content},
        timeout=10,
    )
    if not response.ok:
        raise RuntimeError(
            f"Discord webhook HTTP {response.status_code}: {response.text[:200]}"
        )


def _fmt_px(value: float) -> str:
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _hold_label(opened_at: str) -> str:
    raw = (opened_at or "").strip()
    if not raw:
        return "—"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
    except ValueError:
        return "—"
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def notify_rsi_rev_open(
    symbol: str,
    side: str,
    *,
    zone: str,
    anchor: float,
    entry: float,
    tp: float,
    size: float,
    margin_usdt: float,
) -> None:
    """Discord when an RSI-rev lot is opened. Fail-soft. No REST balance fetch."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return
        title = f"{symbol.upper()} {side.upper()} mở — {zone}"
        body = (
            f"anchor={_fmt_px(anchor)}\n"
            f"entry={_fmt_px(entry)}\n"
            f"target TP={_fmt_px(tp)}\n"
            f"size={size:g}  margin={margin_usdt:.2f} USDT"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_rsi_rev_open failed: %s", exc)


def notify_rsi_rev_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    zone: str,
    anchor: float,
    entry: float,
    tp: float,
    close_price: float,
    pnl_usdt: float,
    opened_at: str = "",
) -> None:
    """Discord when an RSI-rev lot is closed. Fail-soft. No REST balance fetch."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return
        title = f"{symbol.upper()} {side.upper()} đóng — {reason}"
        body = (
            f"vùng RSI: {zone}\n"
            f"anchor={_fmt_px(anchor)}  entry={_fmt_px(entry)}  target TP={_fmt_px(tp)}\n"
            f"đóng={_fmt_px(close_price)}\n"
            f"pnl={pnl_usdt:+.2f} USDT (đã trừ phí)\n"
            f"giữ {_hold_label(opened_at)}"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_rsi_rev_close failed: %s", exc)


_NOISY_LOGGERS = frozenset({"uvicorn", "uvicorn.error", "asyncio", "httpx", "urllib3", "websockets"})


class DiscordErrorLogHandler(logging.Handler):
    """Forward ERROR+ logs to Discord. Fail-soft; cooldown per logger name."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        root = (record.name or "root").split(".", 1)[0]
        if record.name in _NOISY_LOGGERS or root in _NOISY_LOGGERS:
            return
        if getattr(record, "skip_discord", False):
            return
        try:
            notify_error(record.name or "error", self.format(record))
        except Exception:  # noqa: BLE001
            pass


def notify_spot_transfer(
    *,
    transfer_date: str,
    status: str,
    amount: float,
    detail: str,
    day_pnl: float = 0.0,
    dd_pct: float = 0.0,
    equity: float = 0.0,
    peak: float = 0.0,
) -> None:
    """Discord daily futures→spot decision (success / skipped / failed). Fail-soft."""
    try:
        if not discord_configured():
            return
        title = f"Spot transfer {transfer_date} — {status}"
        body = (
            f"{detail}\n"
            f"amount={amount:.2f} {MARGIN_COIN} | day_pnl={day_pnl:+.2f} | "
            f"DD={dd_pct*100:.1f}% | equity={equity:.2f} | peak={peak:.2f}"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_spot_transfer failed: %s", exc)


def notify_risk_warning(kind: str, message: str) -> None:
    """Discord risk warn (DD / maint / initial). Fail-soft."""
    try:
        if not discord_configured():
            return
        _send_discord(f"Risk warn — {kind}", message)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_risk_warning failed: %s", exc)


def notify_error(context: str, detail: str, *, cooldown_sec: float | None = None) -> None:
    """Send bot-error notification to Discord. Never raises; no balance fetch (avoid API during bans)."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return

        window = _ERROR_NOTIFY_COOLDOWN_SEC if cooldown_sec is None else max(0.0, cooldown_sec)
        key = context.strip() or "error"
        now = time.monotonic()
        last = _last_error_notify_at.get(key, 0.0)
        if window > 0 and (now - last) < window:
            logging.debug("Discord error notify cooldown: %s", key)
            return
        _last_error_notify_at[key] = now

        title = f"Bot lỗi: {key}"
        body = str(detail).strip() or "(no detail)"
        try:
            _send_discord(title, body)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logging.warning("Discord notify send failed: %s", exc, extra={"skip_discord": True})
    except Exception as extra_exc:  # noqa: BLE001 — never break trading
        logging.warning("Discord notify_error failed: %s", extra_exc, extra={"skip_discord": True})


def install_error_hooks() -> None:
    """Discord on uncaught exceptions (main + threads) and ERROR logs."""
    import sys
    import traceback

    root = logging.getLogger()
    if not any(isinstance(h, DiscordErrorLogHandler) for h in root.handlers):
        handler = DiscordErrorLogHandler(level=logging.ERROR)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)

    def _hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc, tb))[-1500:]
        notify_error("uncaught", f"{exc_type.__name__}: {exc}\n{detail}", cooldown_sec=30)
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args) -> None:
        if args.exc_type is None or issubclass(args.exc_type, KeyboardInterrupt):
            return
        name = args.thread.name if args.thread else "thread"
        detail = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )[-1500:]
        notify_error(
            f"thread {name}",
            f"{args.exc_type.__name__}: {args.exc_value}\n{detail}",
            cooldown_sec=30,
        )

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
