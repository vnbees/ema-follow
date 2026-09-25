"""Live vs Config A backtest reference metrics for dashboard."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src import database as db
from src.config import INITIAL_CAPITAL

VN = ZoneInfo("Asia/Ho_Chi_Minh")

# Paper BT Config A · 365d · live skim (docs/backtest_channel_vol_A_spot_twoway.md)
BT_REF = {
    "label": "BT Config A · 365d · live skim",
    "wr_pct": 71.5,
    "trades_per_day": 28.6,
    "pf": 1.43,
    "pct_per_day": 1.538,
    "maxdd_futures_pct": 39.9,
    "maxdd_total_pct": 34.8,
}


def _verdict_wr(live: float | None, bt: float) -> str:
    if live is None:
        return "—"
    if abs(live - bt) <= 3.0:
        return "Khớp"
    return "Cao hơn" if live > bt else "Thấp hơn"


def _verdict_rate(live: float | None, bt: float, *, tol_pct: float = 15.0) -> str:
    if live is None or bt <= 0:
        return "—"
    if abs(live - bt) / bt * 100 <= tol_pct:
        return "Khớp"
    return "Cao hơn" if live > bt else "Thấp hơn"


def _verdict_pf(live: float | None, bt: float) -> str:
    if live is None:
        return "—"
    if live >= bt * 0.95:
        return "Khớp"
    if live >= 1.15:
        return "OK"
    return "Yếu hơn"


def _verdict_pct_day(live: float | None, bt: float) -> str:
    if live is None:
        return "—"
    if live >= bt * 0.5:
        return "Gần BT"
    if live >= 0:
        return "Dưới kỳ vọng"
    return "Dưới kỳ vọng"


def _verdict_dd(live: float | None, bt: float) -> str:
    if live is None:
        return "—"
    if live <= bt:
        return "Trong envelope"
    return "Vượt BT"


def _verdict_total(vs_pct: float | None) -> str:
    if vs_pct is None:
        return "—"
    if vs_pct >= 5:
        return "Trên vốn"
    if vs_pct >= -2:
        return "Gần hòa"
    return "Dưới vốn"


def build_live_vs_bt() -> dict:
    """Compute live window metrics vs static BT Config A reference."""
    baseline = db.get_baseline_equity()
    principal = float(baseline) if baseline is not None and baseline > 0 else float(INITIAL_CAPITAL)
    skim_tot, _skim_n = db.sum_successful_spot_transfers()

    with db.get_connection() as conn:
        first = conn.execute(
            "SELECT recorded_at, equity FROM equity_snapshots ORDER BY recorded_at ASC LIMIT 1"
        ).fetchone()
        last = conn.execute(
            "SELECT recorded_at, equity FROM equity_snapshots ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
        snaps = conn.execute(
            "SELECT recorded_at, equity FROM equity_snapshots ORDER BY recorded_at"
        ).fetchall()
        closed = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
                   COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS gp,
                   COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS gl
            FROM donchian_lots
            WHERE status = 'closed' AND IFNULL(close_reason, '') != 'RESET_FRESH'
            """
        ).fetchone()
        skims = conn.execute(
            """
            SELECT transfer_date, amount FROM spot_transfers WHERE status = 'success'
            """
        ).fetchall()

    by_day: dict = {}
    for row in snaps:
        dt = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00")).astimezone(VN).date()
        by_day[dt] = float(row["equity"])
    days = sorted(by_day)

    live_days = 0
    first_eq = float(first["equity"]) if first else 0.0
    last_eq = float(last["equity"]) if last else 0.0
    if days:
        live_days = (days[-1] - days[0]).days + 1

    n = int(closed["n"] or 0) if closed else 0
    wins = int(closed["wins"] or 0) if closed else 0
    gp = float(closed["gp"] or 0) if closed else 0.0
    gl = float(closed["gl"] or 0) if closed else 0.0
    wr = (100.0 * wins / n) if n else None
    pf = (gp / abs(gl)) if gl < 0 else (None if n == 0 else float("inf"))
    if pf == float("inf"):
        pf = None
    tpd = (n / live_days) if live_days > 0 else None

    total_wealth = last_eq + float(skim_tot or 0)
    pct_day = None
    total_ret_pct = None
    if live_days > 1 and first_eq > 0 and total_wealth > 0:
        total_ret_pct = (total_wealth / first_eq - 1.0) * 100.0
        pct_day = total_ret_pct / (live_days - 1)

    # MaxDD futures from EOD
    maxdd_fut = None
    if eqs := [by_day[d] for d in days]:
        peak = eqs[0]
        mdd = 0.0
        for e in eqs:
            peak = max(peak, e)
            if peak > 0:
                mdd = max(mdd, (peak - e) / peak * 100.0)
        maxdd_fut = mdd

    skim_by_day: dict[str, float] = {}
    for row in skims:
        key = str(row["transfer_date"])
        skim_by_day[key] = skim_by_day.get(key, 0.0) + float(row["amount"] or 0)

    maxdd_tot = None
    if days:
        cum = 0.0
        wealth: list[float] = []
        for d in days:
            cum += skim_by_day.get(d.isoformat(), 0.0)
            wealth.append(by_day[d] + cum)
        peak_w = wealth[0]
        mdd_w = 0.0
        for w in wealth:
            peak_w = max(peak_w, w)
            if peak_w > 0:
                mdd_w = max(mdd_w, (peak_w - w) / peak_w * 100.0)
        maxdd_tot = mdd_w

    vs_principal_pct = None
    if principal > 0:
        vs_principal_pct = (total_wealth / principal - 1.0) * 100.0

    bt = BT_REF
    rows = [
        {
            "metric": "WR",
            "live": wr,
            "live_txt": f"{wr:.1f}%" if wr is not None else "—",
            "bt_txt": f"~{bt['wr_pct']:.1f}%",
            "verdict": _verdict_wr(wr, bt["wr_pct"]),
        },
        {
            "metric": "Lệnh/ngày",
            "live": tpd,
            "live_txt": f"{tpd:.1f}" if tpd is not None else "—",
            "bt_txt": f"{bt['trades_per_day']:.1f}",
            "verdict": _verdict_rate(tpd, bt["trades_per_day"]),
        },
        {
            "metric": "PF (lệnh đóng)",
            "live": pf,
            "live_txt": f"{pf:.2f}" if pf is not None else "—",
            "bt_txt": f"~{bt['pf']:.2f}",
            "verdict": _verdict_pf(pf, bt["pf"]),
        },
        {
            "metric": "%/ngày total",
            "live": pct_day,
            "live_txt": f"{pct_day:+.2f}%" if pct_day is not None else "—",
            "bt_txt": f"+{bt['pct_per_day']:.2f}%",
            "verdict": _verdict_pct_day(pct_day, bt["pct_per_day"]),
        },
        {
            "metric": "MaxDD futures",
            "live": maxdd_fut,
            "live_txt": f"{maxdd_fut:.1f}%" if maxdd_fut is not None else "—",
            "bt_txt": f"~{bt['maxdd_futures_pct']:.0f}%",
            "verdict": _verdict_dd(maxdd_fut, bt["maxdd_futures_pct"]),
        },
        {
            "metric": "MaxDD total",
            "live": maxdd_tot,
            "live_txt": f"{maxdd_tot:.1f}%" if maxdd_tot is not None else "—",
            "bt_txt": f"~{bt['maxdd_total_pct']:.0f}%",
            "verdict": _verdict_dd(maxdd_tot, bt["maxdd_total_pct"]),
        },
        {
            "metric": "Total vs vốn gốc",
            "live": vs_principal_pct,
            "live_txt": (
                f"{total_wealth:.0f} / {principal:.0f} ({vs_principal_pct:+.1f}%)"
                if vs_principal_pct is not None
                else "—"
            ),
            "bt_txt": "compound mạnh / năm",
            "verdict": _verdict_total(vs_principal_pct),
        },
    ]

    return {
        "bt_label": bt["label"],
        "live_days": live_days,
        "live_label": f"Live (~{live_days}d)" if live_days else "Live",
        "first_equity": first_eq,
        "last_equity": last_eq,
        "total_wealth": total_wealth,
        "principal": principal,
        "total_ret_pct": total_ret_pct,
        "rows": rows,
    }
