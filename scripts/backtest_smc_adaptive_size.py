"""Backtest Adaptive Dynamic Position Sizing trên sổ SMC đã đóng.

Không đổi entry / TP / SL. Chỉ đổi khối lượng.
Cửa sổ: 30 lệnh đã đóng trước thời điểm vào lệnh mới.
Profit_avg = trung bình nửa lệnh thắng lớn nhất trong cửa sổ (không lấy mean toàn bộ).
Loss_max = Profit_avg * K
Volume_raw = Loss_max / SL%
Nếu SL% / TP% > 5 thì hạ về notional sàn 5 USDT, không ép bù phí.
Ngược lại, nếu lãi ròng dự kiến khi chạm TP < 0.05 USDT thì nâng volume vừa đủ +0.05.
Bốn lệnh đầu kỳ dùng đúng notional đã vào trên sổ live.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass

FEE_RT = 0.0018  # 0.18% trên notional vào lệnh, đúng bước kiểm tra phí
MIN_NET = 0.05
MIN_SAFE_NOTIONAL = 5.0
RATIO_MAX = 5.0
WINDOW = 30
COLD_START = 4


@dataclass
class Trade:
    id: int
    symbol: str
    side: str
    entry: float
    stop: float
    tp: float
    fill: float
    close: float
    qty: float
    pnl: float
    reason: str
    opened_at: str
    closed_at: str

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def sl_pct(self) -> float:
        return abs(self.fill - self.stop) / self.fill

    @property
    def tp_pct(self) -> float:
        return abs(self.tp - self.fill) / self.fill


def profit_avg_top_half(wins: list[dict]) -> float | None:
    """Trung bình nửa lệnh thắng lớn nhất. Một lệnh thắng thì lấy chính lệnh đó."""
    if not wins:
        return None
    ordered = sorted((c["pnl"] for c in wins), reverse=True)
    keep = max(1, math.ceil(len(ordered) * 0.5))
    top = ordered[:keep]
    return sum(top) / len(top)


def live_notional(trade: Trade) -> float:
    return trade.qty * trade.fill


def volume_final(loss_max: float | None, sl_pct: float, tp_pct: float) -> tuple[float, str]:
    """Trả volume USDT và lý do (raw, bump, hoặc ratio_guard)."""
    if sl_pct <= 0:
        return 0.0, "sl0"
    if tp_pct <= 0 or (sl_pct / tp_pct) > RATIO_MAX:
        return MIN_SAFE_NOTIONAL, "ratio_guard"
    raw = (loss_max / sl_pct) if loss_max and loss_max > 0 else 0.0
    if tp_pct <= FEE_RT:
        return raw if raw > 0 else MIN_SAFE_NOTIONAL, "raw_fee_gt_tp"
    net = raw * (tp_pct - FEE_RT)
    if net >= MIN_NET:
        return raw, "raw"
    return MIN_NET / (tp_pct - FEE_RT), "bump"


def net_pnl(trade: Trade, volume: float) -> float:
    if volume <= 0 or trade.fill <= 0:
        return 0.0
    qty = volume / trade.fill
    exit_px = trade.stop if trade.reason == "stop" else trade.close
    gross = (exit_px - trade.fill) * qty * trade.direction
    fee = volume * (FEE_RT / 2) + qty * exit_px * (FEE_RT / 2)
    return gross - fee


def replay(trades: list[Trade], k: float) -> dict:
    closed: list[dict] = []
    rows = []
    for index, trade in enumerate(trades):
        window = [c for c in closed if c["closed_at"] <= trade.opened_at][-WINDOW:]
        wins = [c for c in window if c["pnl"] > 0]
        profit_avg = profit_avg_top_half(wins)
        loss_max = profit_avg * k if profit_avg is not None else None
        if index < COLD_START:
            volume, how = live_notional(trade), "cold_start"
        else:
            volume, how = volume_final(loss_max, trade.sl_pct, trade.tp_pct)
            if volume <= 0:
                volume, how = live_notional(trade), "cold_start"
        pnl = net_pnl(trade, volume)
        row = {
            "id": trade.id,
            "symbol": trade.symbol,
            "reason": trade.reason,
            "opened_at": trade.opened_at,
            "closed_at": trade.closed_at,
            "sl_pct": trade.sl_pct,
            "tp_pct": trade.tp_pct,
            "wins_in_window": len(wins),
            "profit_avg": profit_avg,
            "loss_max": loss_max,
            "volume": volume,
            "how": how,
            "pnl": pnl,
            "book_pnl": trade.pnl,
        }
        rows.append(row)
        closed.append(row)
        closed.sort(key=lambda c: c["closed_at"])
    return {"k": k, "rows": rows}


def summarize(result: dict) -> None:
    rows = result["rows"]
    taken = [r for r in rows if r["volume"] > 0]
    skipped = [r for r in rows if r["volume"] <= 0]
    stops = [r for r in taken if r["reason"] == "stop"]
    tps = [r for r in taken if r["reason"] == "tp"]
    wins = [r for r in taken if r["pnl"] > 0]
    book = sum(r["book_pnl"] for r in rows if r["reason"] != "flat")
    sim = sum(r["pnl"] for r in taken if r["reason"] != "flat")
    print(f"K={result['k']}")
    print(f"taken {len(taken)} skipped {len(skipped)} tp {len(tps)} stop {len(stops)}")
    print(f"winrate_tp {len(tps)}/{len([r for r in taken if r['reason'] in ('tp','stop')])}")
    print(f"pnl>0 {len(wins)}/{len(taken)}")
    print(f"sim_net {sim:.4f} book_net {book:.4f}")
    print("STOPS")
    for r in stops:
        print(
            f"  #{r['id']} {r['symbol']} book {r['book_pnl']:.2f} sim {r['pnl']:.2f} "
            f"vol {r['volume']:.1f} how {r['how']} sl {r['sl_pct']*100:.2f}% "
            f"tp {r['tp_pct']*100:.3f}% ratio {r['sl_pct'] / r['tp_pct'] if r['tp_pct'] else 0:.1f} "
            f"wins {r['wins_in_window']} avg {r['profit_avg']}"
        )
    missed_wins = [r for r in skipped if r["book_pnl"] > 0]
    print(f"missed_book_wins {len(missed_wins)}")
    bumps = [r for r in taken if r["how"] != "raw"]
    print(f"resized {len(bumps)}")
    print("FOCUS")
    for r in taken:
        if r["symbol"] in ("SUIUSDT", "DOTUSDT", "AVAXUSDT") and r["reason"] == "stop":
            print(f"  focus #{r['id']} {r['symbol']} {r['pnl']:.4f}")


def load(path: str) -> list[Trade]:
    raw = json.loads(open(path).read())
    trades = []
    for r in raw:
        trades.append(
            Trade(
                id=r["id"],
                symbol=r["symbol"],
                side=r["side"],
                entry=r["entry"],
                stop=r["stop"],
                tp=r["tp"],
                fill=r["fill"],
                close=r["close"],
                qty=r["qty"],
                pnl=r["pnl"],
                reason=r["reason"],
                opened_at=r["opened_at"],
                closed_at=r["closed_at"],
            )
        )
    trades.sort(key=lambda t: (t.opened_at, t.id))
    return trades


if __name__ == "__main__":
    data = load(sys.argv[1])
    for k in (1.2, 1.5):
        summarize(replay(data, k))
        print("---")
