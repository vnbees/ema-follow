"""Discord webhook notifications (close + errors). Fail-soft — never breaks trading."""

from __future__ import annotations

import logging
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


def notify_ema_rsi_open(
    symbol: str,
    side: str,
    *,
    entry: float,
    sl: float,
    tp: float,
    r: float,
    rr: float,
    margin_usdt: float,
) -> None:
    """Discord when EMA-RSI trade is opened. Fail-soft."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return
        title = f"{symbol.upper()} {side.upper()} mở"
        body = (
            f"entry={_fmt_px(entry)}\n"
            f"SL={_fmt_px(sl)}\n"
            f"TP={_fmt_px(tp)}\n"
            f"R={_fmt_px(r)}  RR=1:{rr:g}\n"
            f"margin={margin_usdt:.2f} USDT"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_ema_rsi_open failed: %s", exc)


def notify_ema_rsi_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    entry: float,
    sl: float,
    tp: float,
    close_price: float,
    pnl_usdt: float,
) -> None:
    """Discord when EMA-RSI SL/TP (or invalid-SL flatten) hits. Fail-soft."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return
        label = reason.replace("_", " ").strip()
        title = f"{symbol.upper()} {side.upper()} đóng — {label}"
        pnl_s = f"{pnl_usdt:+.2f}"
        body = (
            f"entry={_fmt_px(entry)}  SL={_fmt_px(sl)}  TP={_fmt_px(tp)}\n"
            f"close={_fmt_px(close_price)}  pnl={pnl_s} USDT"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_ema_rsi_close failed: %s", exc)


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
            logging.warning("Discord notify send failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — never break trading
        logging.warning("Discord notify_error failed: %s", exc)
