"""Close-action notifications via Discord webhook (fail-soft)."""

from __future__ import annotations

import logging

import requests

from src.config import (
    DEFAULT_SYMBOL,
    DISCORD_WEBHOOK_URL,
    MARGIN_COIN,
)
from src.exchange import ExchangeClientError, fetch_futures_balance, has_credentials


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


def notify_close(symbol: str, detail: str) -> None:
    """Send close notification to Discord. Never raises to callers."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return

        title = f"{symbol.upper()} đóng {detail}"
        body = _format_balance_body()
        try:
            _send_discord(title, body)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logging.warning("Discord notify send failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — never break trading
        logging.warning("Discord notify_close failed: %s", exc)
