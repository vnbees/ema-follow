"""Binance W3W Prediction REST (api.binance.com). Read-only helpers + optional trade."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from src.config import BINANCE_API_KEY, BINANCE_SECRET_KEY
from src.exchange.binance_auth import auth_headers, signed_params
from src.predict5m.config import FAPI_BASE, SAPI_BASE

log = logging.getLogger(__name__)

WEI = 10**18
_SLUG = {"5m": "updown-5m-", "15m": "updown-15m-", "1h": "updown-1h-"}
_SLUG_5M = _SLUG["5m"]
_SYM_SLUG = {"BTCUSDT": "btc", "ETHUSDT": "eth", "BNBUSDT": "bnb"}


def usdt_to_wei(usdt: float) -> str:
    """BUY amountIn is wei (18 decimals). 1 USDT → 1000000000000000000."""
    return str(int(round(float(usdt) * WEI)))


def is_updown_topic(
    topic: dict, symbol: str | None = None, interval: str = "5m"
) -> bool:
    """True only for CRYPTO_UP_DOWN of this interval. `5m` in `updown-15m` is not 5m."""
    needle = _SLUG.get(interval)
    if not needle:
        return False
    slug = str(topic.get("slug") or "")
    if needle not in slug:
        return False
    if symbol and str(topic.get("symbol") or "").upper() != symbol.upper():
        return False
    return True


def is_5m_updown_topic(topic: dict, symbol: str | None = None) -> bool:
    return is_updown_topic(topic, symbol, "5m")


def market_is_open(topic: dict) -> bool:
    for m in topic.get("markets") or []:
        if str(m.get("tradingStatus") or "").upper() == "OPEN":
            return True
    return str(topic.get("tradingStatus") or "").upper() == "OPEN"


def pick_outcome(topic: dict, side: str) -> dict | None:
    want = "up" if str(side).upper() == "UP" else "down"
    for m in topic.get("markets") or []:
        for o in m.get("outcomes") or []:
            name = str(o.get("name") or o.get("outcome") or "").strip().lower()
            if name == want:
                return o
        name = str(m.get("outcome") or m.get("name") or m.get("title") or "").lower()
        if want in name and (m.get("tokenId") or m.get("id")):
            return m
    return None


def _sapi(method: str, path: str, params: dict[str, Any] | None = None) -> Any:
    signed = signed_params(BINANCE_API_KEY, BINANCE_SECRET_KEY, params or {})
    url = f"{SAPI_BASE}{path}"
    headers = auth_headers(BINANCE_API_KEY)
    if method == "GET":
        resp = requests.get(url, params=signed, headers=headers, timeout=15)
    else:
        resp = requests.post(url, data=signed, headers=headers, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"{method} {path} HTTP {resp.status_code}: {resp.text[:400]}")
    if not resp.text:
        return {}
    return resp.json()


def list_markets(limit: int = 50) -> list[dict]:
    data = _sapi("GET", "/sapi/v1/w3w/wallet/prediction/market/list", {"limit": int(limit)})
    return list(data.get("marketTopics") or [])


def list_crypto_markets(limit: int = 100) -> list[dict]:
    data = _sapi(
        "GET",
        "/sapi/v1/w3w/wallet/prediction/market/list",
        {
            "l1Category": "crypto",
            "limit": int(limit),
            "sortBy": "CREATED_TIME",
            "orderBy": "DESC",
        },
    )
    return list(data.get("marketTopics") or [])


def prediction_wallet() -> dict:
    data = _sapi("GET", "/sapi/v1/w3w/wallet/prediction/wallet/list", {})
    wallets = list(data.get("wallets") or [])
    if not wallets:
        raise RuntimeError("no prediction wallet on this API key")
    return wallets[0]


def payment_options() -> list[dict]:
    data = _sapi("GET", "/sapi/v1/w3w/wallet/prediction/balance/payment-options", {})
    if isinstance(data, dict):
        return list(data.get("items") or [])
    return list(data or [])


def parse_available_usdt(
    items: list[dict],
    prefer: tuple[str, ...] = ("CEDEFI", "SPOT", "FUNDING"),
) -> float:
    """Usable USDT. Live fills debit CeDeFi even though place sends accountType SPOT."""
    by_type: dict[str, float] = {}
    for row in items:
        key = str(row.get("accountType") or "").upper()
        raw = row.get("availableBalanceDisplay") or row.get("availableBalance") or 0
        try:
            by_type[key] = float(raw)
        except (TypeError, ValueError):
            by_type[key] = 0.0
    for name in prefer:
        if name in by_type:
            return by_type[name]
    return max(by_type.values(), default=0.0)


def available_usdt() -> float:
    return parse_available_usdt(payment_options())


def market_detail(topic_id: int) -> dict:
    return _sapi(
        "GET",
        "/sapi/v1/w3w/wallet/prediction/market/detail",
        {"marketTopicId": int(topic_id)},
    )


def find_updown(symbol: str, window_start_ms: int, interval: str = "5m") -> dict | None:
    """Match CRYPTO_UP_DOWN market for symbol + interval whose window starts at window_start_ms."""
    want = symbol.upper()
    start = int(window_start_ms)
    unix = str(start // 1000)
    needle = _SLUG.get(interval, f"updown-{interval}-")
    for topic in list_crypto_markets():
        if not is_updown_topic(topic, want, interval):
            continue
        if int(topic.get("startDate") or 0) == start:
            return topic
        if str(topic.get("slug") or "").endswith(unix):
            return topic
    prefix = _SYM_SLUG.get(want, want.replace("USDT", "").lower())
    try:
        data = _sapi(
            "GET",
            "/sapi/v1/w3w/wallet/prediction/market/search",
            {"query": f"{prefix}-{needle}{unix}", "limit": 10},
        )
    except Exception as e:
        log.warning("predict5m %s market search skip: %s", interval, e)
        return None
    rows = data if isinstance(data, list) else list((data or {}).get("marketTopics") or [])
    for topic in rows:
        if is_updown_topic(topic, want, interval):
            return topic
    return None


def find_5m_updown(symbol: str, window_start_ms: int) -> dict | None:
    return find_updown(symbol, window_start_ms, "5m")


def _rows(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("orders", "items", "list", "orderHistory", "rows", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            nested = _rows(val)
            if nested:
                return nested
    return []


def order_history(*, wallet_address: str, limit: int = 50, **extra: Any) -> list[dict]:
    params: dict[str, Any] = {"walletAddress": wallet_address, "limit": int(limit)}
    params.update({k: v for k, v in extra.items() if v is not None})
    return _rows(_sapi("GET", "/sapi/v1/w3w/wallet/prediction/order/history", params))


def active_orders(*, wallet_address: str, limit: int = 50, **extra: Any) -> list[dict]:
    params: dict[str, Any] = {"walletAddress": wallet_address, "limit": int(limit)}
    params.update({k: v for k, v in extra.items() if v is not None})
    return _rows(_sapi("GET", "/sapi/v1/w3w/wallet/prediction/order/list", params))


def find_order(order_id: str) -> dict | None:
    want = str(order_id)
    wallet = prediction_wallet()
    addr = wallet["walletAddress"]
    for row in active_orders(wallet_address=addr) + order_history(wallet_address=addr):
        if str(row.get("orderId") or row.get("id") or "") == want:
            return row
    return None


def cancel_orders(order_ids: list[str]) -> Any:
    wallet = prediction_wallet()
    ids = [str(x) for x in order_ids if str(x).strip()]
    if not ids:
        return {}
    return _sapi(
        "POST",
        "/sapi/v1/w3w/wallet/prediction/trade/batch-cancel",
        {
            "walletAddress": wallet["walletAddress"],
            "walletId": wallet["walletId"],
            "cancelInfoList": json.dumps([{"orderId": oid} for oid in ids]),
        },
    )


def fetch_klines(symbol: str, limit: int = 200, interval: str = "5m") -> list[list]:
    url = f"{FAPI_BASE}/fapi/v1/klines"
    resp = requests.get(
        url,
        params={
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": int(limit),
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"klines {symbol} HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()
