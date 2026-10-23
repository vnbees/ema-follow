"""Prediction Up/Down BTC/ETH/BNB on 5m, 15m, 1h. Default off — do not start until asked."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

ENABLED = os.getenv("PREDICT5M_ENABLED", "false").lower() in ("1", "true", "yes", "on")
TRADING_ENABLED = os.getenv("PREDICT5M_TRADING_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SYMBOLS: tuple[str, ...] = tuple(
    s.strip().upper()
    for s in os.getenv("PREDICT5M_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT").split(",")
    if s.strip()
)
STAKE_USDT = float(os.getenv("PREDICT5M_STAKE_USDT", "1"))
# Profit of a fresh bet. After a filled loss: next_target = streak_loss + this (gỡ lỗ + 2).
TARGET_USDT = float(os.getenv("PREDICT5M_TARGET_USDT", os.getenv("PREDICT5M_STAKE_USDT", "2")))
# Stake = target / WIN_PROFIT_MULT because LIMIT pays ≥ 2× stake.
PAUSE_SEC = int(os.getenv("PREDICT5M_PAUSE_SEC", "3600"))
TIMEFRAMES: tuple[str, ...] = tuple(
    s.strip()
    for s in os.getenv("PREDICT5M_TIMEFRAMES", "5m,15m,1h").split(",")
    if s.strip()
)
INTERVAL_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}
INTERVAL = "5m"
M5_MS = INTERVAL_MS["5m"]
# Place only in the first seconds of the window (not mid-candle).
PLACE_GRACE_MS = int(os.getenv("PREDICT5M_PLACE_GRACE_MS", "20000"))
# 15m/1h keep a longer open-window so a 5m place at :00 can finish first.
PLACE_GRACE_HTF_MS = int(os.getenv("PREDICT5M_PLACE_GRACE_HTF_MS", "60000"))


def interval_ms(timeframe: str) -> int:
    try:
        return INTERVAL_MS[timeframe]
    except KeyError as exc:
        raise ValueError(f"unsupported predict timeframe: {timeframe}") from exc


def place_grace_ms(timeframe: str) -> int:
    if timeframe == "5m":
        return PLACE_GRACE_MS
    return max(PLACE_GRACE_MS, PLACE_GRACE_HTF_MS)
# Win profit must be at least this multiple of stake (2 → lãi gấp đôi vốn).
WIN_PROFIT_MULT = float(os.getenv("PREDICT5M_WIN_PROFIT_MULT", "2"))
# Even-money after ~2% Prediction fee: win +0.98S / lose −S (legacy fallback).
FEE_WIN_FRAC = 0.98
KLINE_LIMIT = 200
SAPI_BASE = os.getenv("BINANCE_SAPI_BASE", "https://api.binance.com").rstrip("/")
FAPI_BASE = os.getenv("BINANCE_API_BASE", "https://fapi.binance.com").rstrip("/")
SQZ_LEN = 20
SQZ_MULT_KC = 1.5
