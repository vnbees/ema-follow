"""Place an Up/Down LIMIT on 5m/15m/1h. No-op unless PREDICT5M_TRADING_ENABLED.

LIMIT at a price where a win pays at least 2× stake. GTC rests until the
Prediction window closes (exchange cancels unfilled). FOK market is not used.
"""

from __future__ import annotations

import logging
from typing import Any

from src.predict5m import api
from src.predict5m.config import TRADING_ENABLED, WIN_PROFIT_MULT
from src.predict5m.engine import target_limit_price

log = logging.getLogger(__name__)


def quote_body(
    *,
    wallet_address: str,
    token_id: str,
    amount_wei: str,
    slippage_bps: int,
    chain_id: str = "56",
    order_type: str = "LIMIT",
    price_limit: str | float | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "walletAddress": wallet_address,
        "tokenId": token_id,
        "side": "BUY",
        "amountIn": str(amount_wei),
        "orderType": str(order_type or "LIMIT"),
        "slippageBps": int(slippage_bps),
        "chainId": str(chain_id or "56"),
    }
    if price_limit is not None:
        body["priceLimit"] = str(price_limit)
    return body


def place_body(
    *,
    quote_id: str,
    slippage_bps: int,
    wallet_address: str,
    wallet_id: str,
    order_type: str = "LIMIT",
    price_limit: str | float | None = None,
    time_in_force: str = "GTC",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "quoteId": quote_id,
        "slippageBps": int(slippage_bps),
        "orderType": str(order_type or "LIMIT"),
        "timeInForce": str(time_in_force or "GTC"),
        "accountType": "SPOT",
        "walletAddress": wallet_address,
        "walletId": wallet_id,
    }
    if price_limit is not None:
        body["priceLimit"] = str(price_limit)
    return body


def _unwrap(resp: Any) -> dict:
    if isinstance(resp, dict) and isinstance(resp.get("data"), dict):
        return resp["data"]
    return resp if isinstance(resp, dict) else {}


def _market_decimals(topic: dict) -> int:
    for m in topic.get("markets") or []:
        raw = m.get("decimalPrecision")
        if raw is not None:
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                pass
    return 2


def _fee_bps(topic: dict) -> float:
    try:
        return float(topic.get("feeRateBps") or 200)
    except (TypeError, ValueError):
        return 200.0


def place(
    symbol: str,
    side: str,
    stake: float,
    window_start_ms: int,
    interval: str = "5m",
) -> dict[str, Any]:
    """side is UP or DOWN. Dry-run unless TRADING_ENABLED. LIMIT at 2×-profit price."""
    topic = api.find_updown(symbol, window_start_ms, interval)
    fee_bps = _fee_bps(topic or {})
    decimals = _market_decimals(topic or {})
    limit_px = target_limit_price(fee_bps, profit_mult=WIN_PROFIT_MULT, decimals=decimals)
    payload = {
        "symbol": symbol.upper(),
        "side": side,
        "stake": float(stake),
        "window_start_ms": int(window_start_ms),
        "interval": interval,
        "market_topic_id": (topic or {}).get("marketTopicId"),
        "dry_run": not TRADING_ENABLED,
        "limit_px": limit_px,
        "fee_bps": fee_bps,
        "order_type": "LIMIT",
    }
    if topic is None:
        payload["error"] = f"no matching {interval} up-down market"
        log.warning("predict5m skip %s: %s", symbol, payload["error"])
        return payload
    if not api.market_is_open(topic):
        payload["error"] = "market not OPEN"
        return payload
    if not TRADING_ENABLED:
        log.info(
            "predict5m dry-run LIMIT %s %s %.2f @%.4f topic=%s",
            symbol,
            side,
            stake,
            limit_px,
            payload["market_topic_id"],
        )
        return payload
    outcome = api.pick_outcome(topic, side)
    if not outcome:
        try:
            topic = api.market_detail(int(topic["marketTopicId"]))
            payload["market_topic_id"] = topic.get("marketTopicId")
            fee_bps = _fee_bps(topic)
            decimals = _market_decimals(topic)
            limit_px = target_limit_price(
                fee_bps, profit_mult=WIN_PROFIT_MULT, decimals=decimals
            )
            payload["limit_px"] = limit_px
            payload["fee_bps"] = fee_bps
            outcome = api.pick_outcome(topic, side)
        except Exception as e:
            payload["error"] = f"market detail: {e}"
            return payload
    if not outcome:
        payload["error"] = "cannot map UP/DOWN outcome token"
        log.warning("predict5m %s", payload["error"])
        return payload
    token_id = outcome.get("tokenId") or outcome.get("id")
    if not token_id:
        payload["error"] = "outcome has no tokenId"
        return payload
    try:
        wallet = api.prediction_wallet()
        addr = wallet["walletAddress"]
        wid = wallet["walletId"]
        wei = api.usdt_to_wei(stake)
        slip = int(topic.get("slippageBps") or 1000)
        price = f"{limit_px:.{decimals}f}"
        quote = _unwrap(
            api._sapi(
                "POST",
                "/sapi/v1/w3w/wallet/prediction/trade/get-quote",
                quote_body(
                    wallet_address=addr,
                    token_id=str(token_id),
                    amount_wei=wei,
                    slippage_bps=slip,
                    chain_id=str(topic.get("chainId") or "56"),
                    order_type="LIMIT",
                    price_limit=price,
                ),
            )
        )
        quote_id = quote.get("quoteId")
        if not quote_id:
            payload["error"] = f"no quoteId: {quote}"
            return payload
        quoted = str(quote.get("amountIn") or "")
        if (
            quoted
            and quoted != wei
            and str(quote.get("orderType") or "LIMIT").upper() != "LIMIT"
        ):
            payload["error"] = f"quote amountIn {quoted} != {wei}"
            payload["quote"] = quote
            return payload
        order = api._sapi(
            "POST",
            "/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle",
            place_body(
                quote_id=str(quote_id),
                slippage_bps=int(quote.get("slippageBps") or slip),
                wallet_address=addr,
                wallet_id=str(wid),
                order_type="LIMIT",
                price_limit=str(quote.get("priceLimit") or price),
                time_in_force="GTC",
            ),
        )
    except Exception as e:
        payload["error"] = str(e)[:400]
        log.warning("predict5m place fail %s %s: %s", symbol, side, payload["error"])
        return payload
    payload["quote"] = quote
    payload["order"] = order if isinstance(order, dict) else {}
    payload["order_id"] = (payload["order"] or {}).get("orderId")
    payload["token_id"] = str(token_id)
    log.info(
        "predict5m LIMIT %s %s %.2f @%s order=%s",
        symbol,
        side,
        stake,
        price,
        payload["order_id"],
    )
    return payload


def cancel(order_id: str) -> Any:
    return api.cancel_orders([order_id])
