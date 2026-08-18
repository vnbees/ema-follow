#!/usr/bin/env python3
"""Backtest LINKUSDT 5m: fade RSI 60/40 cross, scale in on losing candles, exit at anchor extreme.

Short (RSI vượt lên 60):
  - Neo = lần RSI cross lên 60 gần nhất; target = low nến neo
  - Hợp lệ khi chưa có nến nào đóng < target
  - Mỗi nến xanh: short (bỏ qua nếu vị thế đang không lỗ)
  - Đóng khi giá chạm target

Long: đối xứng với RSI 40, target = high, vào trên nến đỏ.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SYMBOL = "LINKUSDT"
INTERVAL = "5m"
BAR_MS = 5 * 60 * 1000
RSI_PERIOD = 14
RSI_SHORT_LEVEL = 60.0
RSI_LONG_LEVEL = 40.0
LOOKBACK_DAYS = 7
WARMUP_DAYS = 7
NOTIONAL_USDT = 100.0
FEE_RATE = 0.0004  # 0.04%/side, gần taker Binance Futures
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC = timezone.utc

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "backtest_LINK_5m_rsi60_7d.md"


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Lot:
    side: str
    entry: float
    qty: float
    opened_at: int
    setup_ts: int
    target: float
    setup_rsi: float


@dataclass
class Trade:
    id: int
    side: str
    opened_at: int
    closed_at: int | None
    entry: float
    exit: float | None
    qty: float
    target: float
    setup_ts: int
    setup_rsi: float
    reason: str
    pnl_usdt: float = 0.0
    fee_usdt: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class Setup:
    side: str
    ts: int
    target: float
    rsi: float
    idx: int


@dataclass
class Book:
    setup: Setup | None = None
    lots: list[Lot] = field(default_factory=list)


def _ms_to_local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def _fetch_klines(start_ms: int, end_ms: int) -> list[Candle]:
    out: list[Candle] = []
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
        req = urllib.request.Request(url, headers={"User-Agent": "ema-rsi-backtest/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode())
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            if ts < start_ms or ts >= end_ms:
                continue
            out.append(
                Candle(
                    ts=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        last_ts = int(rows[-1][0])
        nxt = last_ts + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt
    out.sort(key=lambda c: c.ts)
    dedup: list[Candle] = []
    seen: set[int] = set()
    for c in out:
        if c.ts in seen:
            continue
        seen.add(c.ts)
        dedup.append(c)
    return dedup


def compute_rsi(candles: list[Candle], period: int = RSI_PERIOD) -> list[float | None]:
    n = len(candles)
    rsi: list[float | None] = [None] * n
    if n < period + 1:
        return rsi
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        change = candles[i].close - candles[i - 1].close
        avg_gain += max(change, 0.0)
        avg_loss += max(-change, 0.0)
    avg_gain /= period
    avg_loss /= period
    rsi[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, n):
        change = candles[i].close - candles[i - 1].close
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        rsi[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return rsi


def _avg_entry(lots: list[Lot]) -> float:
    notional = sum(l.entry * l.qty for l in lots)
    qty = sum(l.qty for l in lots)
    return notional / qty if qty else 0.0


def _is_losing(side: str, avg_entry: float, price: float) -> bool:
    if side == "short":
        return price > avg_entry + 1e-12
    return price < avg_entry - 1e-12


def _close_lots(
    book: Book,
    *,
    exit_price: float,
    closed_at: int,
    reason: str,
    trades: list[Trade],
    next_id: int,
) -> int:
    for lot in book.lots:
        fee = (lot.entry + exit_price) * lot.qty * FEE_RATE
        if lot.side == "short":
            gross = (lot.entry - exit_price) * lot.qty
        else:
            gross = (exit_price - lot.entry) * lot.qty
        net = gross - fee
        trades.append(
            Trade(
                id=next_id,
                side=lot.side,
                opened_at=lot.opened_at,
                closed_at=closed_at,
                entry=lot.entry,
                exit=exit_price,
                qty=lot.qty,
                target=lot.target,
                setup_ts=lot.setup_ts,
                setup_rsi=lot.setup_rsi,
                reason=reason,
                pnl_usdt=net,
                fee_usdt=fee,
                pnl_pct=(net / (lot.entry * lot.qty) * 100.0) if lot.entry * lot.qty else 0.0,
            )
        )
        next_id += 1
    book.lots = []
    return next_id


def run_backtest(candles: list[Candle], rsi: list[float | None], trade_from_ms: int) -> tuple[list[Trade], Book, Book, dict]:
    shorts = Book()
    longs = Book()
    trades: list[Trade] = []
    next_id = 1
    skipped = {"short_not_losing": 0, "long_not_losing": 0, "short_no_setup": 0, "long_no_setup": 0}
    setups_seen = {"short": 0, "long": 0}

    for i, c in enumerate(candles):
        if c.ts < trade_from_ms:
            # Vẫn cập nhật setup trong warmup để neo "lần gần nhất" đúng lúc cửa sổ 7 ngày bắt đầu.
            prev_rsi = rsi[i - 1] if i else None
            curr_rsi = rsi[i]
            if prev_rsi is not None and curr_rsi is not None:
                if prev_rsi <= RSI_SHORT_LEVEL < curr_rsi:
                    shorts.setup = Setup("short", c.ts, c.low, curr_rsi, i)
                    setups_seen["short"] += 1
                if prev_rsi >= RSI_LONG_LEVEL > curr_rsi:
                    longs.setup = Setup("long", c.ts, c.high, curr_rsi, i)
                    setups_seen["long"] += 1
            if shorts.setup and c.close < shorts.setup.target:
                shorts.setup = None
            if longs.setup and c.close > longs.setup.target:
                longs.setup = None
            continue

        # 1) Exit trước (chạm target trong nến)
        if shorts.lots and shorts.setup and c.low <= shorts.setup.target:
            next_id = _close_lots(
                shorts,
                exit_price=shorts.setup.target,
                closed_at=c.ts,
                reason="HIT_TARGET",
                trades=trades,
                next_id=next_id,
            )
        if longs.lots and longs.setup and c.high >= longs.setup.target:
            next_id = _close_lots(
                longs,
                exit_price=longs.setup.target,
                closed_at=c.ts,
                reason="HIT_TARGET",
                trades=trades,
                next_id=next_id,
            )

        # 2) Invalidation theo nến đóng xuyên target
        if shorts.setup and c.close < shorts.setup.target:
            shorts.setup = None
        if longs.setup and c.close > longs.setup.target:
            longs.setup = None

        # 3) Neo RSI mới (lần gần nhất)
        prev_rsi = rsi[i - 1] if i else None
        curr_rsi = rsi[i]
        if prev_rsi is not None and curr_rsi is not None:
            if prev_rsi <= RSI_SHORT_LEVEL < curr_rsi:
                shorts.setup = Setup("short", c.ts, c.low, curr_rsi, i)
                setups_seen["short"] += 1
                for lot in shorts.lots:
                    lot.target = shorts.setup.target
                    lot.setup_ts = shorts.setup.ts
                    lot.setup_rsi = shorts.setup.rsi
            if prev_rsi >= RSI_LONG_LEVEL > curr_rsi:
                longs.setup = Setup("long", c.ts, c.high, curr_rsi, i)
                setups_seen["long"] += 1
                for lot in longs.lots:
                    lot.target = longs.setup.target
                    lot.setup_ts = longs.setup.ts
                    lot.setup_rsi = longs.setup.rsi

        is_green = c.close > c.open
        is_red = c.close < c.open

        # 4) Entry
        if is_green:
            if shorts.setup is None:
                skipped["short_no_setup"] += 1
            else:
                if shorts.lots and not _is_losing("short", _avg_entry(shorts.lots), c.close):
                    skipped["short_not_losing"] += 1
                else:
                    qty = NOTIONAL_USDT / c.close
                    shorts.lots.append(
                        Lot(
                            side="short",
                            entry=c.close,
                            qty=qty,
                            opened_at=c.ts,
                            setup_ts=shorts.setup.ts,
                            target=shorts.setup.target,
                            setup_rsi=shorts.setup.rsi,
                        )
                    )
        if is_red:
            if longs.setup is None:
                skipped["long_no_setup"] += 1
            else:
                if longs.lots and not _is_losing("long", _avg_entry(longs.lots), c.close):
                    skipped["long_not_losing"] += 1
                else:
                    qty = NOTIONAL_USDT / c.close
                    longs.lots.append(
                        Lot(
                            side="long",
                            entry=c.close,
                            qty=qty,
                            opened_at=c.ts,
                            setup_ts=longs.setup.ts,
                            target=longs.setup.target,
                            setup_rsi=longs.setup.rsi,
                        )
                    )

    last = candles[-1]
    if shorts.lots:
        next_id = _close_lots(
            shorts,
            exit_price=last.close,
            closed_at=last.ts,
            reason="EOD_OPEN",
            trades=trades,
            next_id=next_id,
        )
    if longs.lots:
        next_id = _close_lots(
            longs,
            exit_price=last.close,
            closed_at=last.ts,
            reason="EOD_OPEN",
            trades=trades,
            next_id=next_id,
        )

    stats = {"skipped": skipped, "setups_seen": setups_seen}
    return trades, shorts, longs, stats


def _fmt(x: float, n: int = 4) -> str:
    if abs(x) >= 100:
        return f"{x:.2f}"
    return f"{x:.{n}f}"


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    line = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return f"{line}\n{sep}\n{body}"


def build_report(
    candles: list[Candle],
    rsi: list[float | None],
    trades: list[Trade],
    stats: dict,
    trade_from_ms: int,
    now_ms: int,
) -> str:
    window = [c for c in candles if c.ts >= trade_from_ms]
    first, last = window[0], window[-1]
    closed = [t for t in trades if t.reason == "HIT_TARGET"]
    eod = [t for t in trades if t.reason == "EOD_OPEN"]
    wins = [t for t in closed if t.pnl_usdt > 0]
    losses = [t for t in closed if t.pnl_usdt <= 0]
    shorts = [t for t in trades if t.side == "short"]
    longs = [t for t in trades if t.side == "long"]

    def _sum(xs: list[Trade]) -> float:
        return sum(t.pnl_usdt for t in xs)

    def _side_block(side: str, xs: list[Trade]) -> str:
        hit = [t for t in xs if t.reason == "HIT_TARGET"]
        w = [t for t in hit if t.pnl_usdt > 0]
        l = [t for t in hit if t.pnl_usdt <= 0]
        wr = (len(w) / len(hit) * 100) if hit else 0.0
        return (
            f"- Số lệnh: {len(xs)} (đóng target {len(hit)}, còn mở cuối kỳ {len(xs) - len(hit)})\n"
            f"- PnL đóng: {_sum(hit):+.4f} USDT | PnL gồm EOD: {_sum(xs):+.4f} USDT\n"
            f"- Win rate (HIT_TARGET): {len(w)}/{len(hit)} = {wr:.1f}%\n"
            f"- Trung bình / lệnh đóng: {(_sum(hit) / len(hit) if hit else 0):+.4f} USDT"
        )

    # Daily pnl (UTC+7 date of close)
    daily: dict[str, float] = {}
    daily_n: dict[str, int] = {}
    for t in trades:
        if t.closed_at is None:
            continue
        day = datetime.fromtimestamp(t.closed_at / 1000, TZ).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + t.pnl_usdt
        daily_n[day] = daily_n.get(day, 0) + 1

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    for t in sorted(trades, key=lambda x: (x.closed_at or 0, x.id)):
        equity += t.pnl_usdt
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        curve.append(equity)

    hold_mins = []
    for t in closed:
        hold_mins.append((t.closed_at - t.opened_at) / 60000)

    trade_rows = []
    for t in trades:
        hold = ""
        if t.closed_at is not None:
            hold = f"{(t.closed_at - t.opened_at) / 60000:.0f}m"
        trade_rows.append(
            [
                str(t.id),
                t.side,
                _ms_to_local(t.opened_at),
                _ms_to_local(t.closed_at) if t.closed_at else "",
                _fmt(t.entry, 4),
                _fmt(t.exit, 4) if t.exit is not None else "",
                _fmt(t.target, 4),
                _ms_to_local(t.setup_ts),
                f"{t.setup_rsi:.2f}",
                f"{t.qty:.4f}",
                f"{t.pnl_usdt:+.4f}",
                f"{t.pnl_pct:+.3f}%",
                f"{t.fee_usdt:.4f}",
                hold,
                t.reason,
            ]
        )

    wr = (len(wins) / len(closed) * 100) if closed else 0.0
    profit_factor = (
        (sum(t.pnl_usdt for t in wins) / abs(sum(t.pnl_usdt for t in losses)))
        if losses and sum(t.pnl_usdt for t in losses) != 0
        else math.inf if wins else 0.0
    )

    lines = [
        f"# Backtest LINKUSDT 5m — fade RSI 60/40",
        "",
        f"- Sinh lúc: {datetime.fromtimestamp(now_ms / 1000, TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Symbol: `{SYMBOL}` · khung `5m` · sàn Binance USDT-M",
        f"- Cửa sổ giao dịch: **{_ms_to_local(first.ts)} → {_ms_to_local(last.ts + BAR_MS)}** (UTC+7)",
        f"- Số nến trong cửa sổ: {len(window)} · warmup RSI: {WARMUP_DAYS} ngày trước đó",
        f"- Giá đầu/cuối cửa sổ: {first.close:.4f} → {last.close:.4f} ({(last.close / first.close - 1) * 100:+.2f}%)",
        "",
        "## Logic đã code",
        "",
        "Sizing: **100 USDT notional / lot** (không đòn bẩy trong PnL). Phí **0.04%/side** (taker).",
        "Fill vào: **close nến tín hiệu**. Fill ra: **đúng giá target** nếu wick chạm (không gap).",
        "",
        "### Short",
        "1. Neo = lần **RSI(14) cắt lên trên 60** gần nhất (`prev ≤ 60 < curr`).",
        "2. Target = **low** của nến neo.",
        "3. Setup còn sống khi **chưa có nến nào đóng dưới target**.",
        "4. Mỗi **nến xanh** (`close > open`): vào short tại close.",
        "5. Bỏ qua nếu đã có lot và **giá hiện tại ≤ avg entry** (entry chưa lỗ).",
        "6. Đóng toàn bộ lot khi `low ≤ target`.",
        "7. Nến đóng < target → hủy setup. Cần RSI cắt lên 60 lần mới mới vào lại.",
        "",
        "### Long (đối xứng)",
        "1. Neo = lần **RSI(14) cắt xuống dưới 40** gần nhất.",
        "2. Target = **high** nến neo.",
        "3. Setup sống khi chưa có nến đóng **trên** target.",
        "4. Mỗi **nến đỏ**: vào long tại close, bỏ qua nếu entry chưa lỗ.",
        "5. Đóng khi `high ≥ target`.",
        "",
        "Hai chiều độc lập (có thể long và short cùng lúc). Lệnh còn mở cuối kỳ mark-to-market (`EOD_OPEN`).",
        "",
        "## Kết quả tổng",
        "",
        _md_table(
            ["Chỉ số", "Giá trị"],
            [
                ["Tổng lot (kể cả EOD)", str(len(trades))],
                ["Đóng vì HIT_TARGET", str(len(closed))],
                ["Còn mở cuối kỳ (EOD)", str(len(eod))],
                ["Win / Loss (HIT_TARGET)", f"{len(wins)} / {len(losses)}"],
                ["Win rate HIT_TARGET", f"{wr:.1f}%"],
                ["PnL đóng HIT_TARGET", f"{_sum(closed):+.4f} USDT"],
                ["PnL EOD mark", f"{_sum(eod):+.4f} USDT"],
                ["PnL tổng", f"{_sum(trades):+.4f} USDT"],
                ["Phí tổng", f"{sum(t.fee_usdt for t in trades):.4f} USDT"],
                ["Avg PnL / lot đóng", f"{(_sum(closed) / len(closed) if closed else 0):+.4f} USDT"],
                ["Best lot", f"{max((t.pnl_usdt for t in trades), default=0):+.4f} USDT"],
                ["Worst lot", f"{min((t.pnl_usdt for t in trades), default=0):+.4f} USDT"],
                ["Profit factor (HIT_TARGET)", f"{profit_factor:.2f}" if profit_factor != math.inf else "∞"],
                ["Max equity DD (theo lot đóng)", f"{max_dd:+.4f} USDT"],
                ["Hold TB (HIT_TARGET)", f"{(sum(hold_mins) / len(hold_mins) if hold_mins else 0):.0f} phút"],
                ["Hold min / max", (
                    f"{min(hold_mins):.0f} / {max(hold_mins):.0f} phút" if hold_mins else "—"
                )],
                ["Số lần neo RSI short / long", f"{stats['setups_seen']['short']} / {stats['setups_seen']['long']}"],
                ["Skip short vì chưa lỗ", str(stats["skipped"]["short_not_losing"])],
                ["Skip long vì chưa lỗ", str(stats["skipped"]["long_not_losing"])],
            ],
        ),
        "",
        "## Theo chiều",
        "",
        "### Short",
        _side_block("short", shorts),
        "",
        "### Long",
        _side_block("long", longs),
        "",
        "## PnL theo ngày đóng (UTC+7)",
        "",
        _md_table(
            ["Ngày", "Số lot đóng", "PnL USDT"],
            [[d, str(daily_n[d]), f"{daily[d]:+.4f}"] for d in sorted(daily)],
        ),
        "",
        "## Chi tiết từng lot",
        "",
        "Notional mỗi lot ≈ 100 USDT. `setup` = nến RSI cross tạo target.",
        "",
        _md_table(
            [
                "#",
                "Side",
                "Vào (UTC+7)",
                "Ra (UTC+7)",
                "Entry",
                "Exit",
                "Target",
                "Setup nến",
                "RSI neo",
                "Qty",
                "PnL USDT",
                "PnL %",
                "Fee",
                "Hold",
                "Reason",
            ],
            trade_rows,
        ),
        "",
        "## Ghi chú đọc report",
        "",
        "- Nhiều lot cùng setup = **trung bình giá khi đang lỗ** (nến xanh/đỏ tiếp theo).",
        "- `EOD_OPEN` không phải hit target — chỉ đóng sổ 7 ngày.",
        "- Không có SL; lot có thể lỗ kéo dài tới khi chạm target hoặc hết cửa sổ.",
        "- RSI 40 cho long là giả định đối xứng với 60 (bạn chỉ nêu 60 cho short).",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    now = datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    # Chỉ nến đã đóng
    last_closed = (now_ms // BAR_MS) * BAR_MS
    trade_to_ms = last_closed
    trade_from_ms = trade_to_ms - LOOKBACK_DAYS * 24 * 60 * 60 * 1000
    fetch_from_ms = trade_from_ms - WARMUP_DAYS * 24 * 60 * 60 * 1000

    candles = _fetch_klines(fetch_from_ms, trade_to_ms)
    if len(candles) < RSI_PERIOD + 50:
        raise SystemExit(f"Không đủ nến: {len(candles)}")
    rsi = compute_rsi(candles)
    trades, _s, _l, stats = run_backtest(candles, rsi, trade_from_ms)
    report = build_report(candles, rsi, trades, stats, trade_from_ms, now_ms)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    closed = [t for t in trades if t.reason == "HIT_TARGET"]
    print(f"Wrote {REPORT_PATH}")
    print(f"trades={len(trades)} hit_target={len(closed)} pnl={sum(t.pnl_usdt for t in trades):+.4f}")


if __name__ == "__main__":
    main()
