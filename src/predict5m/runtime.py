"""Scan closed bars on 5m/15m/1h and (optionally) place one Prediction bet.

Does not start from the live futures cycle. Call only when PREDICT5M_ENABLED.
Places at the open of each window, not mid-candle. One bet at a time; smaller
timeframe wins when several windows open together. Martingale is shared.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from src.notify import (
    notify_error,
    notify_predict5m_open,
    notify_predict5m_pause,
    notify_predict5m_result,
    notify_predict5m_resume,
)
from src.predict5m import broker, store
from src.predict5m.api import available_usdt, fetch_klines, find_order
from src.predict5m.config import (
    ENABLED,
    KLINE_LIMIT,
    PAUSE_SEC,
    TARGET_USDT,
    SYMBOLS,
    TIMEFRAMES,
    TRADING_ENABLED,
    interval_ms,
    place_grace_ms,
)
from src.predict5m.engine import (
    Bankroll,
    after_recovery,
    bankroll_from_loss,
    cannot_afford,
    order_is_filled,
    pick_first,
    resolve_window,
    settle_pnl,
    signal_on_close,
    stake_from_target,
    window_fresh,
)

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _arrays(rows: list[list]):
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = np.array([float(r[4]) for r in rows])
    close_ts = np.array([int(r[6]) for r in rows], dtype=np.int64)
    return ts, o, h, l, c, close_ts


def _tf(row: dict | None, default: str = "5m") -> str:
    if not row:
        return default
    return str(row.get("timeframe") or default)


def _maybe_resume(now_ms: int) -> None:
    if store.clear_pause_if_due(now_ms):
        stake = stake_from_target(TARGET_USDT)
        log.info("predict5m resume after 1h pause, stake=%.2f", stake)
        notify_predict5m_resume(stake=stake)


def scan_once(now_ms: int | None = None, *, timeframe: str = "5m") -> dict:
    """Evaluate last closed bar of `timeframe`. Rest a LIMIT at the 2×-profit price."""
    store.ensure_schema()
    if not ENABLED:
        return {"skipped": "PREDICT5M_ENABLED=false", "timeframe": timeframe}
    now_ms = int(now_ms or _now_ms())
    _maybe_resume(now_ms)
    if store.is_paused(now_ms):
        return {
            "skipped": "paused 1h: wallet cannot cover next stake",
            "timeframe": timeframe,
        }
    if store.open_bet() is not None:
        return {"skipped": "one bet already open", "timeframe": timeframe}

    found = []
    for symbol in SYMBOLS:
        rows = fetch_klines(symbol, KLINE_LIMIT, interval=timeframe)
        if not rows:
            continue
        ts, o, h, l, c, close_ts = _arrays(rows)
        sig = signal_on_close(
            symbol, ts, o, h, l, c, close_ts, now_ms, interval=timeframe
        )
        found.append(sig)
    chosen = pick_first(found)
    if chosen is None:
        return {"skipped": "no valid signal", "timeframe": timeframe}
    if store.has_bar(chosen.symbol, chosen.bar_ts, timeframe):
        return {
            "skipped": "already used this bar",
            "symbol": chosen.symbol,
            "timeframe": timeframe,
        }
    if not window_fresh(chosen.entry_ts, now_ms, grace_ms=place_grace_ms(timeframe)):
        return {
            "skipped": f"not at {timeframe} open",
            "symbol": chosen.symbol,
            "entry_ts": chosen.entry_ts,
            "timeframe": timeframe,
        }

    bank = store.bankroll()
    stake = stake_from_target(bank.target)
    try:
        wallet = available_usdt()
    except Exception as exc:
        notify_error(f"predict5m {timeframe} wallet", str(exc))
        return {"skipped": f"wallet: {exc}", "timeframe": timeframe}
    if cannot_afford(stake, wallet):
        return _pause_broke(now_ms, stake=stake, available=wallet, interval=timeframe)
    side = "UP" if chosen.side == 1 else "DOWN"
    placed = broker.place(
        chosen.symbol, side, stake, chosen.entry_ts, interval=timeframe
    )
    if placed.get("error"):
        notify_error(f"predict5m {timeframe} place", str(placed["error"]))
        return {"skipped": placed["error"], "signal": chosen, "timeframe": timeframe}
    if TRADING_ENABLED and not placed.get("order"):
        notify_error(f"predict5m {timeframe} place", "trading on but order missing")
        return {
            "skipped": "trading on but order missing",
            "placed": placed,
            "timeframe": timeframe,
        }

    row = store.insert_open(
        symbol=chosen.symbol,
        side=side,
        stake=stake,
        double_step=0,
        target_usdt=bank.target,
        bar_ts=chosen.bar_ts,
        entry_ts=chosen.entry_ts,
        market_topic_id=placed.get("market_topic_id"),
        order_id=placed.get("order_id"),
        limit_px=placed.get("limit_px"),
        fee_bps=placed.get("fee_bps"),
        fill_status="SUBMITTED",
        timeframe=timeframe,
    )
    log.info(
        "predict5m %s LIMIT %s %s stake=%.2f target=%.2f @%s bar=%s dry=%s",
        timeframe,
        chosen.symbol,
        side,
        stake,
        bank.target,
        placed.get("limit_px"),
        chosen.bar_ts,
        placed.get("dry_run"),
    )
    notify_predict5m_open(
        chosen.symbol,
        side,
        stake=stake,
        step=0,
        target=bank.target,
        window_start_ms=chosen.entry_ts,
        dry_run=bool(placed.get("dry_run")),
        limit_px=placed.get("limit_px"),
        interval=timeframe,
    )
    return {"opened": row, "placed": placed, "signal": chosen, "timeframe": timeframe}


def scan_all(now_ms: int | None = None) -> list[dict]:
    """Try smaller timeframes first. Stop after the first open bet."""
    now_ms = int(now_ms or _now_ms())
    out: list[dict] = []
    ordered = tuple(sorted(TIMEFRAMES, key=interval_ms))
    for tf in ordered:
        result = scan_once(now_ms, timeframe=tf)
        out.append(result)
        if result.get("opened"):
            break
        if result.get("skipped") in (
            "one bet already open",
            "paused 1h: wallet cannot cover next stake",
            "PREDICT5M_ENABLED=false",
        ):
            break
    return out


def _pause_broke(
    now_ms: int, *, stake: float, available: float, interval: str = "5m"
) -> dict:
    until = store.start_pause(int(now_ms), PAUSE_SEC * 1000)
    log.info(
        "predict5m pause 1h until %s: next stake=%.2f > wallet=%.2f",
        until,
        stake,
        available,
    )
    notify_predict5m_pause(
        hours=PAUSE_SEC / 3600.0,
        until_ms=until,
        stake=stake,
        available=available,
        interval=interval,
    )
    return {
        "skipped": "paused: not enough wallet",
        "paused": True,
        "pause_until_ms": until,
        "next_step": 0,
        "stake": stake,
        "available": available,
        "timeframe": interval,
    }


def settle_open(
    won: bool | None,
    pnl: float,
    reason: str,
    now_ms: int | None = None,
    *,
    filled: bool = True,
    fill_status: str | None = None,
    open_row: dict | None = None,
    timeframe: str = "5m",
) -> dict:
    store.ensure_schema()
    open_row = open_row or store.open_bet()
    if open_row is None:
        return {"skipped": "no open bet", "timeframe": timeframe}
    timeframe = _tf(open_row, timeframe)
    store.close_bet(
        int(open_row["id"]),
        won=None if not filled else bool(won),
        pnl=pnl,
        reason=reason,
        fill_status=fill_status,
    )
    now_ms = int(now_ms or _now_ms())
    this_target = float(open_row.get("target_usdt") or store.bankroll().target)
    nxt = after_recovery(
        bool(won),
        Bankroll(target=this_target, streak_loss=store.bankroll().streak_loss),
        filled=filled,
        stake=float(open_row["stake"]),
    )
    next_stake = stake_from_target(nxt.target)
    notify_predict5m_result(
        str(open_row["symbol"]),
        str(open_row["side"]),
        won=None if not filled else bool(won),
        filled=filled,
        stake=float(open_row["stake"]),
        pnl=pnl,
        reason=reason,
        step=0,
        interval=timeframe,
        target=this_target,
        next_target=nxt.target,
        next_stake=next_stake,
    )
    if not filled:
        return {
            "closed": open_row["id"],
            "won": None,
            "filled": False,
            "next_target": nxt.target,
            "paused": False,
            "reason": reason,
            "timeframe": timeframe,
        }
    if won:
        store.set_bankroll(nxt, False)
        return {
            "closed": open_row["id"],
            "won": True,
            "filled": True,
            "next_target": nxt.target,
            "paused": False,
            "timeframe": timeframe,
        }
    try:
        wallet = available_usdt()
    except Exception as exc:
        store.set_bankroll(nxt, False)
        log.warning("predict5m balance check skip after loss: %s", exc)
        return {
            "closed": open_row["id"],
            "won": False,
            "filled": True,
            "next_target": nxt.target,
            "paused": False,
            "balance_error": str(exc),
            "timeframe": timeframe,
        }
    if cannot_afford(next_stake, wallet):
        paused = _pause_broke(
            now_ms, stake=next_stake, available=wallet, interval=timeframe
        )
        return {
            "closed": open_row["id"],
            "won": False,
            "filled": True,
            "next_target": TARGET_USDT,
            "paused": True,
            "pause_until_ms": paused["pause_until_ms"],
            "stake": next_stake,
            "available": wallet,
            "timeframe": timeframe,
        }
    store.set_bankroll(nxt, False)
    return {
        "closed": open_row["id"],
        "won": False,
        "filled": True,
        "next_target": nxt.target,
        "paused": False,
        "timeframe": timeframe,
    }


def _settle_row(open_row: dict, now_ms: int) -> dict:
    timeframe = _tf(open_row)
    bar_ms = interval_ms(timeframe)
    entry_ts = int(open_row["entry_ts"])
    if now_ms < entry_ts + bar_ms + 1000:
        return {"skipped": "window still open", "timeframe": timeframe}
    try:
        rows = fetch_klines(open_row["symbol"], 12, interval=timeframe)
    except Exception as e:
        notify_error(f"predict5m {timeframe} settle", str(e))
        return {"skipped": f"settle klines: {e}", "timeframe": timeframe}
    bar = next(
        (r for r in rows if int(r[0]) == entry_ts and int(r[6]) < now_ms),
        None,
    )
    if bar is None:
        return {"skipped": "settlement bar not closed", "timeframe": timeframe}

    fill_status = str(open_row.get("fill_status") or "")
    filled = True
    order_id = open_row.get("order_id")
    if order_id:
        try:
            order = find_order(str(order_id))
            fill_status = str((order or {}).get("status") or fill_status or "UNKNOWN")
            # Prediction LIMIT dies with the market; do not batch-cancel.
            filled = order_is_filled(fill_status)
            if not filled and order:
                try:
                    filled = float(order.get("filledUsdtAmount") or 0) > 0
                except (TypeError, ValueError):
                    filled = False
            store.set_fill_status(int(open_row["id"]), fill_status)
        except Exception as e:
            notify_error(f"predict5m {timeframe} fill", str(e))
            return {"skipped": f"fill status: {e}", "timeframe": timeframe}
        if not filled:
            return settle_open(
                None,
                0.0,
                f"unfilled:{fill_status}",
                now_ms=now_ms,
                filled=False,
                fill_status=fill_status,
                open_row=open_row,
                timeframe=timeframe,
            )

    won, reason = resolve_window(str(open_row["side"]), float(bar[1]), float(bar[4]))
    pnl = settle_pnl(
        won,
        float(open_row["stake"]),
        filled=True,
        limit_px=open_row.get("limit_px"),
        fee_bps=open_row.get("fee_bps"),
    )
    return settle_open(
        won,
        pnl,
        reason,
        now_ms=now_ms,
        filled=True,
        fill_status=fill_status or "FILLED",
        open_row=open_row,
        timeframe=timeframe,
    )


def settle_due(now_ms: int | None = None, *, timeframe: str | None = None) -> dict:
    """Resolve open bets whose Prediction window has closed."""
    store.ensure_schema()
    now_ms = int(now_ms or _now_ms())
    if timeframe is not None:
        row = store.open_bet()
        rows = [row] if row and _tf(row) == timeframe else []
    else:
        rows = store.open_bets()
    if not rows:
        return {"skipped": "no open bet"}
    out = [_settle_row(row, now_ms) for row in rows]
    if len(out) == 1:
        return out[0]
    return {"settled": out}


def seconds_until_open(now_ms: int | None = None, *, pad_sec: float = 1.2) -> float:
    now_ms = int(now_ms or _now_ms())
    rem = []
    for tf in TIMEFRAMES:
        ms = interval_ms(tf)
        rem.append(ms - (now_ms % ms))
    return min(rem) / 1000.0 + pad_sec


def run_forever() -> None:
    """Wait for the next 5m/15m/1h open, settle, then place. Never called unless ENABLED."""
    store.ensure_schema()
    if store.open_bet() is None:
        store.set_bankroll(bankroll_from_loss(store.bankroll().streak_loss))
    log.info(
        "predict5m loop at each %s open (trading=%s)",
        "/".join(TIMEFRAMES),
        TRADING_ENABLED,
    )
    try:
        for result in scan_all():
            log.info(
                "predict5m %s boot scan %s",
                result.get("timeframe"),
                result.get("skipped") or result,
            )
    except Exception as exc:
        log.exception("predict5m boot scan failed")
        notify_error("predict5m boot", str(exc))
    while True:
        time.sleep(max(0.2, seconds_until_open()))
        try:
            settled = settle_due()
            if settled.get("settled"):
                for row in settled["settled"]:
                    if not row.get("skipped"):
                        log.info("predict5m settled %s", row)
                    elif row.get("skipped") != "no open bet":
                        log.info("predict5m settle skip %s", row.get("skipped"))
            elif not settled.get("skipped"):
                log.info("predict5m settled %s", settled)
            elif settled.get("skipped") != "no open bet":
                log.info("predict5m settle skip %s", settled.get("skipped"))
            for placed in scan_all():
                tf = placed.get("timeframe")
                if placed.get("skipped"):
                    log.info("predict5m %s skip %s", tf, placed.get("skipped"))
                else:
                    log.info("predict5m %s tick %s", tf, placed)
        except Exception as exc:
            log.exception("predict5m tick failed")
            notify_error("predict5m tick", str(exc))


def main() -> None:
    from src.config import LOG_DIR

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "predict5m.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    root.handlers = [sh, fh]
    log.info("predict5m log file %s", log_path)
    if not ENABLED:
        print("predict5m idle (PREDICT5M_ENABLED=false). Not scanning.")
        return
    run_forever()


if __name__ == "__main__":
    main()
