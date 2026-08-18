#!/usr/bin/env python3
"""Chạy detect_sideway (docs/detectsideway) trên LINKUSDT 5m 7 ngày."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

SYMBOL = "LINKUSDT"
INTERVAL = "5m"
BAR_MS = 5 * 60 * 1000
LOOKBACK_DAYS = 7
WARMUP_DAYS = 3
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "sideway_LINK_5m_7d.md"


def detect_sideway(
    df: pd.DataFrame,
    window_fast=20,
    window_slow=50,
    threshold_pct=0.003,
    slope_threshold=0.0005,
    lookback=10,
):
    """Copy đúng logic docs/detectsideway."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=window_fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=window_slow, adjust=False).mean()
    df["ema_mid"] = df["close"].ewm(span=int((window_fast + window_slow) / 2), adjust=False).mean()
    df["ema_spread"] = abs(df["ema_fast"] - df["ema_slow"]) / df["close"]
    df["is_converged"] = df["ema_spread"] < threshold_pct
    df["ema_slope"] = (df["ema_mid"] - df["ema_mid"].shift(lookback)) / df["ema_mid"].shift(lookback)
    df["is_flat"] = abs(df["ema_slope"]) < slope_threshold
    df["price_near_ema"] = abs(df["close"] - df["ema_mid"]) / df["ema_mid"] < (threshold_pct * 1.5)
    df["is_sideway"] = (
        df["is_converged"].rolling(window=lookback).mean() >= 0.8
    ) & df["is_flat"] & (df["price_near_ema"].rolling(window=lookback).mean() >= 0.7)
    return df


def classify_trend(row) -> str:
    if bool(row["is_sideway"]):
        return "SIDEWAY"
    if row["ema_fast"] > row["ema_slow"] and row["ema_slope"] > 0:
        return "UPTREND"
    if row["ema_fast"] < row["ema_slow"] and row["ema_slope"] < 0:
        return "DOWNTREND"
    if row["ema_fast"] > row["ema_slow"]:
        return "UP_WEAK"
    if row["ema_fast"] < row["ema_slow"]:
        return "DOWN_WEAK"
    return "UNCLEAR"


def _fetch_klines(start_ms: int, end_ms: int) -> list[dict]:
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "ema-rsi-sideway/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode())
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                out.append(
                    {
                        "ts": ts,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    }
                )
        last_ts = int(rows[-1][0])
        nxt = last_ts + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt
    seen: set[int] = set()
    dedup = []
    for r in sorted(out, key=lambda x: x["ts"]):
        if r["ts"] in seen:
            continue
        seen.add(r["ts"])
        dedup.append(r)
    return dedup


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def _new_regime(label: str, row) -> dict:
    return {
        "trend": label,
        "start_ts": int(row.ts),
        "end_ts": int(row.ts),
        "start_px": float(row.close),
        "end_px": float(row.close),
        "bars": 1,
        "high": float(row.high),
        "low": float(row.low),
    }


def _regimes(df: pd.DataFrame) -> list[dict]:
    regimes = []
    cur = None
    for row in df.itertuples(index=False):
        label = row.trend
        if cur is None or cur["trend"] != label:
            if cur is not None:
                regimes.append(cur)
            cur = _new_regime(label, row)
        else:
            cur["end_ts"] = int(row.ts)
            cur["end_px"] = float(row.close)
            cur["bars"] += 1
            cur["high"] = max(cur["high"], float(row.high))
            cur["low"] = min(cur["low"], float(row.low))
    if cur:
        regimes.append(cur)
    return regimes


def _merge_parts(*parts: dict, trend: str | None = None) -> dict:
    first, last = parts[0], parts[-1]
    return {
        "trend": trend if trend is not None else first["trend"],
        "start_ts": first["start_ts"],
        "end_ts": last["end_ts"],
        "start_px": first["start_px"],
        "end_px": last["end_px"],
        "bars": sum(p["bars"] for p in parts),
        "high": max(p["high"] for p in parts),
        "low": min(p["low"] for p in parts),
    }


def _fuse_same(regimes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in regimes:
        if out and out[-1]["trend"] == r["trend"]:
            out[-1] = _merge_parts(out[-1], r, trend=r["trend"])
        else:
            out.append(r)
    return out


def collapse_weak(regimes: list[dict]) -> list[dict]:
    mapped = []
    for r in regimes:
        lab = r["trend"]
        if lab == "UP_WEAK":
            lab = "UPTREND"
        elif lab == "DOWN_WEAK":
            lab = "DOWNTREND"
        mapped.append({**r, "trend": lab})
    return _fuse_same(mapped)


def absorb_sideway(regimes: list[dict]) -> list[dict]:
    """Down → sideway → down = downtrend (tương tự up). Sideway giữa 2 chiều trái ngược giữ nguyên."""
    out = list(regimes)
    changed = True
    while changed:
        changed = False
        nxt: list[dict] = []
        i = 0
        while i < len(out):
            if (
                i + 2 < len(out)
                and out[i + 1]["trend"] == "SIDEWAY"
                and out[i]["trend"] in ("UPTREND", "DOWNTREND")
                and out[i]["trend"] == out[i + 2]["trend"]
            ):
                nxt.append(_merge_parts(out[i], out[i + 1], out[i + 2], trend=out[i]["trend"]))
                i += 3
                changed = True
            else:
                nxt.append(out[i])
                i += 1
        fused = _fuse_same(nxt)
        if len(fused) != len(out) or any(a["bars"] != b["bars"] for a, b in zip(fused, out)):
            changed = True
        out = fused
    # Sideway đầu/cuối cửa sổ: gắn vào trend kề
    if len(out) >= 2 and out[0]["trend"] == "SIDEWAY" and out[1]["trend"] != "SIDEWAY":
        out = [_merge_parts(out[0], out[1], trend=out[1]["trend"]), *out[2:]]
    if len(out) >= 2 and out[-1]["trend"] == "SIDEWAY" and out[-2]["trend"] != "SIDEWAY":
        out = [*out[:-2], _merge_parts(out[-2], out[-1], trend=out[-2]["trend"])]
    return _fuse_same(out)


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // BAR_MS) * BAR_MS
    trade_from = last_closed - LOOKBACK_DAYS * 24 * 60 * 60 * 1000
    fetch_from = trade_from - WARMUP_DAYS * 24 * 60 * 60 * 1000
    rows = _fetch_klines(fetch_from, last_closed)
    df = pd.DataFrame(rows)
    df = detect_sideway(df)
    df["trend"] = df.apply(classify_trend, axis=1)

    window = df[df["ts"] >= trade_from].copy()
    window["is_sideway"] = window["is_sideway"].fillna(False)
    raw = _regimes(window)
    collapsed = collapse_weak(raw)
    regimes = absorb_sideway(collapsed)

    n = len(window)
    last = window.iloc[-1]
    first = window.iloc[0]
    absorbed_bars = {k: 0 for k in ("SIDEWAY", "UPTREND", "DOWNTREND")}
    for r in regimes:
        absorbed_bars[r["trend"]] = absorbed_bars.get(r["trend"], 0) + r["bars"]

    def pct(label: str, bars: int) -> str:
        return f"{bars} nến ({bars / n * 100:.1f}%)"

    def regime_row(r: dict) -> list[str]:
        mins = r["bars"] * 5
        h, m = divmod(mins, 60)
        dur = f"{h}h{m:02d}m" if h else f"{m}m"
        move = (r["end_px"] / r["start_px"] - 1) * 100
        rng = (r["high"] / r["low"] - 1) * 100
        return [
            r["trend"],
            _local(r["start_ts"]),
            _local(r["end_ts"] + BAR_MS),
            f"{dur} ({r['bars']} nến)",
            f"{r['start_px']:.4f} → {r['end_px']:.4f}",
            f"{move:+.2f}%",
            f"{rng:.2f}%",
        ]

    current_absorbed = regimes[-1]["trend"] if regimes else last.trend

    lines = [
        f"# Xu hướng LINKUSDT 5m — 7 ngày (gộp sideway vào trend)",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cửa sổ: **{_local(int(first.ts))} → {_local(int(last.ts) + BAR_MS)}** (UTC+7)",
        f"- Nến: {n} · warmup EMA: {WARMUP_DAYS} ngày",
        f"- Giá: {first.close:.4f} → {last.close:.4f} ({(last.close / first.close - 1) * 100:+.2f}%)",
        "",
        "## Quy tắc gộp",
        "",
        "1. `UP_WEAK` → UPTREND, `DOWN_WEAK` → DOWNTREND.",
        "2. **Cùng chiều nuốt sideway:** `DOWNTREND → SIDEWAY → DOWNTREND` = **DOWNTREND** "
        "(tương tự UPTREND).",
        "3. Sideway đầu/cuối cửa sổ gắn vào trend kề.",
        "4. Sideway **giữa hai chiều trái ngược** (up rồi down, hoặc ngược lại) **giữ SIDEWAY** — đây là đoạn chuyển pha.",
        "",
        "## Hiện tại",
        "",
        f"- Nến đóng: **{_local(int(last.ts))}** · close **{last.close:.4f}**",
        f"- Nhãn nến (detector gốc): `{last.trend}`",
        f"- Nhãn đoạn đã gộp: **{current_absorbed}** "
        f"({_local(regimes[-1]['start_ts'])} → {_local(regimes[-1]['end_ts'] + BAR_MS)})",
        "",
        "## Phân bố sau khi gộp",
        "",
        f"- UPTREND: {pct('UPTREND', absorbed_bars['UPTREND'])}",
        f"- DOWNTREND: {pct('DOWNTREND', absorbed_bars['DOWNTREND'])}",
        f"- SIDEWAY (chỉ chuyển pha): {pct('SIDEWAY', absorbed_bars['SIDEWAY'])}",
        f"- Số đoạn: {len(regimes)} (trước gộp: {len(raw)} raw / {len(collapsed)} sau collapse WEAK)",
        "",
        "## Timeline đã gộp",
        "",
        "| Xu hướng | Từ | Đến | Thời lượng | Giá | Change | High-Low |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in regimes:
        lines.append("| " + " | ".join(regime_row(r)) + " |")

    by_label = {"UPTREND": [], "DOWNTREND": [], "SIDEWAY": []}
    for r in regimes:
        by_label[r["trend"]].append(r)

    for label in ("UPTREND", "DOWNTREND", "SIDEWAY"):
        lines += [
            "",
            f"## {label} ({len(by_label[label])} đoạn)",
            "",
            "| Từ | Đến | Thời lượng | Giá | Change |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in by_label[label]:
            row = regime_row(r)
            lines.append("| " + " | ".join(row[1:6]) + " |")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {REPORT_PATH}")
    print(f"absorbed last={current_absorbed} close={last.close:.4f}")
    print(f"bars={absorbed_bars} n_regimes={len(regimes)} raw={len(raw)}")
    for r in regimes:
        print(
            f"{r['trend']:10} {_local(r['start_ts'])} → {_local(r['end_ts'] + BAR_MS)} "
            f"{r['bars'] * 5:>5}p  {r['start_px']:.3f}→{r['end_px']:.3f} "
            f"{(r['end_px'] / r['start_px'] - 1) * 100:+.2f}%"
        )


if __name__ == "__main__":
    main()
