"""Web Push notifications for close actions (fail-soft)."""

from __future__ import annotations

import json
import logging

from src import database as db
from src.config import (
    DEFAULT_SYMBOL,
    MARGIN_COIN,
    VAPID_PRIVATE_KEY,
    VAPID_PUBLIC_KEY,
    VAPID_SUBJECT,
)
from src.exchange import ExchangeClientError, fetch_futures_balance, has_credentials


def vapid_configured() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and VAPID_SUBJECT)


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
        logging.warning("Push notify: balance fetch failed (%s)", exc)
        return f"Futures balance: fetch failed ({MARGIN_COIN})"


def _send_to_subscription(subscription: dict, payload: str) -> None:
    from pywebpush import WebPushException, webpush

    webpush(
        subscription_info=subscription,
        data=payload,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims={"sub": VAPID_SUBJECT},
    )


def notify_close(symbol: str, detail: str) -> None:
    """Send close notification to all push subscriptions. Never raises to callers."""
    try:
        if not vapid_configured():
            logging.debug("Push notify skipped: VAPID not configured")
            return

        subs = db.list_push_subscriptions()
        if not subs:
            logging.debug("Push notify skipped: no subscriptions")
            return

        title = f"{symbol.upper()} đóng {detail}"
        body = _format_balance_body()
        payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)

        from pywebpush import WebPushException

        for row in subs:
            endpoint = str(row["endpoint"])
            subscription = {
                "endpoint": endpoint,
                "keys": {
                    "p256dh": str(row["p256dh"]),
                    "auth": str(row["auth"]),
                },
            }
            try:
                _send_to_subscription(subscription, payload)
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    logging.info("Push endpoint gone (%s) — removing", status)
                    db.delete_push_subscription(endpoint)
                else:
                    logging.warning("Push send failed for %s: %s", endpoint[:48], exc)
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logging.warning("Push send error for %s: %s", endpoint[:48], exc)
    except Exception as exc:  # noqa: BLE001 — never break trading
        logging.warning("Push notify_close failed: %s", exc)
