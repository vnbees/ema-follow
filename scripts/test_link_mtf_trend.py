#!/usr/bin/env python3
"""Trend LINK khi 5m + 1h + 4h cùng hướng (detect_sideway). Không gộp sideway."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

SYMBOL = "LINKUSDT"
LOOKBACK_DAYS = 7
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "trend_align_LINK_mtf_7d.md"

TF = {
    "5m": 5 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}
# EMA50 trên 4h cần ~50 nến ≈ 8 ngày; lấy dư.
WARMUP_MS = {
    "5m": 7 * 24 * 3600 * 1000,
    "1h": 20 * 24 * 3600 * 1000,
    "4h": 40 * 24 * 3600 * 1000,
}


def detect_sideway(
    df: pd.DataFrame,
    window_fast=20,
    window_slow=50,
    threshold_pct=0.003,
    slope_threshold=0.0005,
    lookback=10,
):
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
    if bool(row["is_sideway"]) if pd.notna(row["is_sideway"]) else False:
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


def direction(label: str) -> str:
    if label in ("UPTREND", "UP_WEAK"):
        return "UP"
    if label in ("DOWNTREND", "DOWN_WEAK"):
        return "DOWN"
    return "FLAT"


def fetch_klines(interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    bar_ms = TF[interval]
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "ema-rsi-mtf/1.0"})
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
        nxt = last_ts + bar_ms
        if nxt <= cursor:
            break
        cursor = nxt
    df = pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df


def label_tf(df: pd.DataFrame) -> pd.DataFrame:
    df = detect_sideway(df)
    df["trend"] = df.apply(classify_trend, axis=1)
    df["dir"] = df["trend"].map(direction)
    return df


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def attach_htf(m5: pd.DataFrame, htf: pd.DataFrame, prefix: str, bar_ms: int) -> pd.DataFrame:
    right = htf[["ts", "trend", "dir", "close", "ema_fast", "ema_slow"]].copy()
    right["valid_from"] = right["ts"] + bar_ms
    right = right.rename(
        columns={
            "trend": f"{prefix}_trend",
            "dir": f"{prefix}_dir",
            "close": f"{prefix}_close",
            "ema_fast": f"{prefix}_ema20",
            "ema_slow": f"{prefix}_ema50",
            "ts": f"{prefix}_ts",
        }
    )
    m5 = m5.copy()
    m5["close_ts"] = m5["ts"] + TF["5m"]
    out = pd.merge_asof(
        m5.sort_values("close_ts"),
        right.sort_values("valid_from"),
        left_on="close_ts",
        right_on="valid_from",
        direction="backward",
    )
    return out


def aligned_label(row) -> str:
    d5, d1, d4 = row["dir"], row["h1_dir"], row["h4_dir"]
    if d5 == "UP" and d1 == "UP" and d4 == "UP":
        return "TREND_UP"
    if d5 == "DOWN" and d1 == "DOWN" and d4 == "DOWN":
        return "TREND_DOWN"
    return "NO_TREND"


def compress(df: pd.DataFrame, col: str) -> list[dict]:
    regimes = []
    cur = None
    for row in df.itertuples(index=False):
        lab = getattr(row, col)
        if cur is None or cur["label"] != lab:
            if cur is not None:
                regimes.append(cur)
            cur = {
                "label": lab,
                "start_ts": int(row.ts),
                "end_ts": int(row.ts),
                "start_px": float(row.close),
                "end_px": float(row.close),
                "high": float(row.high),
                "low": float(row.low),
                "bars": 1,
                "t5": getattr(row, "trend"),
                "t1": getattr(row, "h1_trend"),
                "t4": getattr(row, "h4_trend"),
            }
        else:
            cur["end_ts"] = int(row.ts)
            cur["end_px"] = float(row.close)
            cur["high"] = max(cur["high"], float(row.high))
            cur["low"] = min(cur["low"], float(row.low))
            cur["bars"] += 1
    if cur:
        regimes.append(cur)
    return regimes


def episode_stats(ep: dict) -> dict:
    move = (ep["end_px"] / ep["start_px"] - 1) * 100
    rng = (ep["high"] / ep["low"] - 1) * 100
    if ep["label"] == "TREND_UP":
        mfe = (ep["high"] / ep["start_px"] - 1) * 100
        mae = (ep["start_px"] - ep["low"]) / ep["start_px"] * 100
        win = move > 0
    elif ep["label"] == "TREND_DOWN":
        mfe = (ep["start_px"] - ep["low"]) / ep["start_px"] * 100
        mae = (ep["high"] / ep["start_px"] - 1) * 100
        win = move < 0
    else:
        mfe = mae = 0.0
        win = False
    mins = ep["bars"] * 5
    return {
        **ep,
        "move": move,
        "rng": rng,
        "mfe": mfe,
        "mae": mae,
        "win": win,
        "mins": mins,
    }


def _dur(mins: int) -> str:
    h, m = divmod(mins, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed_5m = (now_ms // TF["5m"]) * TF["5m"]
    window_from = last_closed_5m - LOOKBACK_DAYS * 24 * 3600 * 1000

    frames = {}
    for interval in ("5m", "1h", "4h"):
        start = window_from - WARMUP_MS[interval]
        raw = fetch_klines(interval, start, last_closed_5m)
        frames[interval] = label_tf(raw)

    m5 = frames["5m"]
    m5 = m5[m5["ts"] >= window_from].copy()
    m5 = attach_htf(m5, frames["1h"], "h1", TF["1h"])
    m5 = attach_htf(m5, frames["4h"], "h4", TF["4h"])
    m5 = m5.dropna(subset=["h1_dir", "h4_dir"]).copy()
    m5["aligned"] = m5.apply(aligned_label, axis=1)

    n = len(m5)
    first, last = m5.iloc[0], m5.iloc[-1]
    counts = m5["aligned"].value_counts()
    regimes = [episode_stats(r) for r in compress(m5, "aligned")]
    trends = [r for r in regimes if r["label"] in ("TREND_UP", "TREND_DOWN")]
    ups = [r for r in trends if r["label"] == "TREND_UP"]
    downs = [r for r in trends if r["label"] == "TREND_DOWN"]

    def win_rate(xs):
        if not xs:
            return 0.0
        return sum(1 for x in xs if x["win"]) / len(xs) * 100

    def avg(xs, key):
        if not xs:
            return 0.0
        return sum(x[key] for x in xs) / len(xs)

    # 5m-only vs 3TF: trên các nến 5m đang UPTREND/DOWNTREND, bao nhiêu % được 1h+4h xác nhận
    m5_up = m5[m5["dir"] == "UP"]
    m5_down = m5[m5["dir"] == "DOWN"]
    conf_up = ((m5_up["h1_dir"] == "UP") & (m5_up["h4_dir"] == "UP")).mean() * 100 if len(m5_up) else 0
    conf_down = ((m5_down["h1_dir"] == "DOWN") & (m5_down["h4_dir"] == "DOWN")).mean() * 100 if len(m5_down) else 0

    # Hướng giá theo nến 5m-only (không cần HTF) vs aligned
    def signed_move(df, dcol):
        # average next-bar close-to-close in direction of signal at this bar
        nxt = df["close"].shift(-1)
        ret = (nxt - df["close"]) / df["close"] * 100
        sign = df[dcol].map({"UP": 1, "DOWN": -1, "TREND_UP": 1, "TREND_DOWN": -1}).fillna(0)
        mask = sign != 0
        if mask.sum() == 0:
            return 0.0, 0.0
        edge = (ret[mask] * sign[mask]).dropna()
        return float(edge.mean()), float((edge > 0).mean() * 100)

    e5, h5 = signed_move(m5.assign(sig=m5["dir"]), "sig")
    ea, ha = signed_move(m5.assign(sig=m5["aligned"]), "sig")

    last_row = m5.iloc[-1]
    has_trend = last_row["aligned"] != "NO_TREND"

    def pct_n(label: str) -> str:
        c = int(counts.get(label, 0))
        return f"{c} nến 5m ({c / n * 100:.1f}%)"

    def ep_row(r: dict) -> list[str]:
        return [
            r["label"].replace("TREND_", ""),
            _local(r["start_ts"]),
            _local(r["end_ts"] + TF["5m"]),
            _dur(r["mins"]),
            f"{r['start_px']:.4f} → {r['end_px']:.4f}",
            f"{r['move']:+.2f}%",
            f"{r['mfe']:+.2f}%",
            f"{r['mae']:+.2f}%",
            "win" if r["win"] else "lose",
            f"{r['t5']} / {r['t1']} / {r['t4']}",
        ]

    lines = [
        f"# Trend đa khung LINKUSDT — 5m ∩ 1h ∩ 4h",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cửa sổ 5m: **{_local(int(first.ts))} → {_local(int(last.ts) + TF['5m'])}** (UTC+7)",
        f"- Nến 5m: {n} · giá {first.close:.4f} → {last.close:.4f} "
        f"({(last.close / first.close - 1) * 100:+.2f}%)",
        "",
        "## Quy tắc",
        "",
        "Detector `docs/detectsideway` chạy **riêng** trên 5m, 1h, 4h (cùng tham số). **Không gộp sideway.**",
        "",
        "- `UP` = UPTREND hoặc UP_WEAK (EMA20 > EMA50).",
        "- `DOWN` = DOWNTREND hoặc DOWN_WEAK.",
        "- `FLAT` = SIDEWAY.",
        "- **TREND_UP** chỉ khi 5m + 1h + 4h đều `UP`.",
        "- **TREND_DOWN** chỉ khi cả 3 đều `DOWN`.",
        "- Khác → **NO_TREND** (kể cả 5m đang trend nhưng 1h/4h lệch hoặc sideway).",
        "- 1h/4h lấy nến **đã đóng** tại thời điểm đóng nến 5m (không lookahead).",
        "",
        "## Hiện tại",
        "",
        f"- 5m {_local(int(last_row.ts))}: **{last_row.trend}** ({last_row.dir}) close {last_row.close:.4f}",
        f"- 1h (nến đóng {_local(int(last_row.h1_ts))}): **{last_row.h1_trend}** ({last_row.h1_dir})",
        f"- 4h (nến đóng {_local(int(last_row.h4_ts))}): **{last_row.h4_trend}** ({last_row.h4_dir})",
        f"- Kết luận 3 khung: **{last_row.aligned}**"
        + (" — được phép coi là có trend" if has_trend else " — **chưa có trend**"),
        "",
        "## Hiệu quả 7 ngày",
        "",
        f"- TREND_UP: {pct_n('TREND_UP')}",
        f"- TREND_DOWN: {pct_n('TREND_DOWN')}",
        f"- Có trend (up+down): "
        f"{int(counts.get('TREND_UP', 0) + counts.get('TREND_DOWN', 0)) / n * 100:.1f}% thời gian",
        f"- NO_TREND: {pct_n('NO_TREND')}",
        f"- Số episode có trend: {len(trends)} (up {len(ups)}, down {len(downs)})",
        f"- Win rate episode (giá đóng cuối vs đầu, đúng hướng): "
        f"{win_rate(trends):.0f}% ({sum(1 for x in trends if x['win'])}/{len(trends)})",
        f"  - UP: {win_rate(ups):.0f}% ({sum(1 for x in ups if x['win'])}/{len(ups)}) · "
        f"avg move {avg(ups, 'move'):+.2f}% · avg MFE {avg(ups, 'mfe'):+.2f}% · avg MAE {avg(ups, 'mae'):+.2f}%",
        f"  - DOWN: {win_rate(downs):.0f}% ({sum(1 for x in downs if x['win'])}/{len(downs)}) · "
        f"avg move {avg(downs, 'move'):+.2f}% · avg MFE {avg(downs, 'mfe'):+.2f}% · avg MAE {avg(downs, 'mae'):+.2f}%",
        f"- Hold TB / episode: {avg(trends, 'mins'):.0f} phút",
        f"- Edge 1 nến 5m tiếp theo (đúng hướng tín hiệu): "
        f"chỉ 5m = {e5:+.4f}%/nến (hit {h5:.0f}%) · "
        f"3 khung = {ea:+.4f}%/nến (hit {ha:.0f}%)",
        f"- Khi 5m đang UP, 1h+4h cùng UP: **{conf_up:.1f}%** nến",
        f"- Khi 5m đang DOWN, 1h+4h cùng DOWN: **{conf_down:.1f}%** nến",
        "",
        "MFE = chạy thuận tối đa trong episode; MAE = chạy ngược tối đa (từ giá lúc bắt đầu align).",
        "Win = close cuối episode cùng hướng với trend (chưa trừ phí).",
        "",
        "## Các lần TREND (3 khung trùng)",
        "",
        "| Hướng | Từ | Đến | Thời lượng | Giá | Change | MFE | MAE | KQ | 5m / 1h / 4h |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in trends:
        lines.append("| " + " | ".join(ep_row(r)) + " |")

    lines += [
        "",
        "## Timeline đầy đủ (gồm NO_TREND)",
        "",
        "| Nhãn | Từ | Đến | Thời lượng | Giá | Change |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in regimes:
        lines.append(
            "| "
            + " | ".join(
                [
                    r["label"],
                    _local(r["start_ts"]),
                    _local(r["end_ts"] + TF["5m"]),
                    _dur(r["mins"]),
                    f"{r['start_px']:.4f} → {r['end_px']:.4f}",
                    f"{r['move']:+.2f}%",
                ]
            )
            + " |"
        )
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {REPORT_PATH}")
    print(f"n={n} aligned={dict(counts)} episodes={len(trends)} wr={win_rate(trends):.0f}%")
    print(f"now 5m={last_row.trend} 1h={last_row.h1_trend} 4h={last_row.h4_trend} => {last_row.aligned}")
    print(f"5m-only next-bar edge={e5:+.4f}%  3tf edge={ea:+.4f}%")
    print(f"confirm 5m-up {conf_up:.1f}%  5m-down {conf_down:.1f}%")
    for r in trends:
        print(
            f"{r['label']:12} {_local(r['start_ts'])} → {_local(r['end_ts'] + TF['5m'])} "
            f"{_dur(r['mins']):>7} {r['move']:+.2f}% {'W' if r['win'] else 'L'}"
        )


if __name__ == "__main__":
    main()
