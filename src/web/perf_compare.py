"""Live vs Config A backtest reference metrics for dashboard."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src import database as db
from src.config import INITIAL_CAPITAL

VN = ZoneInfo("Asia/Ho_Chi_Minh")

# Deploy resting TP limit on VPS (2026-09-26 11:00 +07).
LIMIT_TP_SINCE = "2026-09-26T04:00:40+00:00"

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


def _parse_dt(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def _window_metrics(
    *,
    snaps: list,
    closed: dict | None,
    skim_by_day: dict[str, float],
    skim_before: float,
    skim_total: float,
    principal: float,
    since: datetime | None,
) -> dict:
    """Metrics for all history (since=None) or from a cut timestamp onward."""
    if since is None:
        snap_rows = list(snaps)
    else:
        snap_rows = [r for r in snaps if _parse_dt(str(r["recorded_at"])) >= since]

    by_day: dict = {}
    for row in snap_rows:
        dt = _parse_dt(str(row["recorded_at"])).astimezone(VN).date()
        by_day[dt] = float(row["equity"])
    days = sorted(by_day)

    live_days = (days[-1] - days[0]).days + 1 if days else 0
    first_eq = float(snap_rows[0]["equity"]) if snap_rows else 0.0
    last_eq = float(snap_rows[-1]["equity"]) if snap_rows else 0.0

    n = int(closed["n"] or 0) if closed else 0
    wins = int(closed["wins"] or 0) if closed else 0
    gp = float(closed["gp"] or 0) if closed else 0.0
    gl = float(closed["gl"] or 0) if closed else 0.0
    wr = (100.0 * wins / n) if n else None
    pf = (gp / abs(gl)) if gl < 0 else None
    tpd = (n / live_days) if live_days > 0 else None

    # Wealth at window start = futures equity + skim already done before window.
    wealth_start = first_eq + float(skim_before or 0)
    total_wealth = last_eq + float(skim_total or 0)
    pct_day = None
    total_ret_pct = None
    if live_days > 1 and wealth_start > 0 and total_wealth > 0:
        total_ret_pct = (total_wealth / wealth_start - 1.0) * 100.0
        pct_day = total_ret_pct / (live_days - 1)

    maxdd_fut = None
    if eqs := [by_day[d] for d in days]:
        peak = eqs[0]
        mdd = 0.0
        for e in eqs:
            peak = max(peak, e)
            if peak > 0:
                mdd = max(mdd, (peak - e) / peak * 100.0)
        maxdd_fut = mdd

    maxdd_tot = None
    if days:
        # skim_by_day for a cut window must already be only skims inside that window.
        cum = 0.0
        wealth: list[float] = []
        for d in days:
            cum += skim_by_day.get(d.isoformat(), 0.0)
            wealth.append(by_day[d] + float(skim_before or 0) + cum)
        peak_w = wealth[0]
        mdd_w = 0.0
        for w in wealth:
            peak_w = max(peak_w, w)
            if peak_w > 0:
                mdd_w = max(mdd_w, (peak_w - w) / peak_w * 100.0)
        maxdd_tot = mdd_w

    vs_principal_pct = None
    if principal > 0 and total_wealth > 0:
        vs_principal_pct = (total_wealth / principal - 1.0) * 100.0

    return {
        "live_days": live_days,
        "first_eq": first_eq,
        "last_eq": last_eq,
        "wealth_start": wealth_start,
        "total_wealth": total_wealth,
        "wr": wr,
        "pf": pf,
        "tpd": tpd,
        "pct_day": pct_day,
        "total_ret_pct": total_ret_pct,
        "maxdd_fut": maxdd_fut,
        "maxdd_tot": maxdd_tot,
        "vs_principal_pct": vs_principal_pct,
        "n": n,
    }


def _closed_stats(conn, *, since: str | None = None):
    if since:
        return conn.execute(
            """
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
                   COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS gp,
                   COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS gl
            FROM donchian_lots
            WHERE status = 'closed' AND IFNULL(close_reason, '') != 'RESET_FRESH'
              AND closed_at >= ?
            """,
            (since,),
        ).fetchone()
    return conn.execute(
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS gp,
               COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS gl
        FROM donchian_lots
        WHERE status = 'closed' AND IFNULL(close_reason, '') != 'RESET_FRESH'
        """
    ).fetchone()


def build_live_vs_bt() -> dict:
    """Compute live window metrics vs static BT Config A reference."""
    baseline = db.get_baseline_equity()
    principal = float(baseline) if baseline is not None and baseline > 0 else float(INITIAL_CAPITAL)
    skim_tot, _skim_n = db.sum_successful_spot_transfers()
    cut = datetime.fromisoformat(LIMIT_TP_SINCE)

    with db.get_connection() as conn:
        snaps = conn.execute(
            "SELECT recorded_at, equity FROM equity_snapshots ORDER BY recorded_at"
        ).fetchall()
        closed_all = _closed_stats(conn)
        closed_limit = _closed_stats(conn, since=LIMIT_TP_SINCE)
        skims = conn.execute(
            "SELECT transfer_date, amount, created_at FROM spot_transfers WHERE status = 'success'"
        ).fetchall()
        skim_before_cut = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS s
            FROM spot_transfers
            WHERE status = 'success' AND created_at < ?
            """,
            (LIMIT_TP_SINCE,),
        ).fetchone()["s"]

    skim_by_day_all: dict[str, float] = {}
    skim_by_day_limit: dict[str, float] = {}
    for row in skims:
        key = str(row["transfer_date"])
        amt = float(row["amount"] or 0)
        skim_by_day_all[key] = skim_by_day_all.get(key, 0.0) + amt
        created = str(row["created_at"] or "")
        if created and _parse_dt(created) >= cut:
            skim_by_day_limit[key] = skim_by_day_limit.get(key, 0.0) + amt

    live = _window_metrics(
        snaps=snaps,
        closed=closed_all,
        skim_by_day=skim_by_day_all,
        skim_before=0.0,
        skim_total=float(skim_tot or 0),
        principal=principal,
        since=None,
    )
    limit = _window_metrics(
        snaps=snaps,
        closed=closed_limit,
        skim_by_day=skim_by_day_limit,
        skim_before=float(skim_before_cut or 0),
        skim_total=float(skim_tot or 0),
        principal=principal,
        since=cut,
    )

    bt = BT_REF
    # Đánh giá so cột "Từ limit" với BT — chế độ đang chạy.
    ref = limit if limit["live_days"] > 0 else live

    def _txt_pct(v: float | None, *, signed: bool = False) -> str:
        if v is None:
            return "—"
        return f"{v:+.2f}%" if signed else f"{v:.1f}%"

    def _txt_num(v: float | None, digits: int = 1) -> str:
        if v is None:
            return "—"
        return f"{v:.{digits}f}"

    rows = [
        {
            "metric": "WR",
            "live_txt": _txt_pct(live["wr"]),
            "limit_txt": _txt_pct(limit["wr"]),
            "bt_txt": f"~{bt['wr_pct']:.1f}%",
            "verdict": _verdict_wr(ref["wr"], bt["wr_pct"]),
        },
        {
            "metric": "Lệnh/ngày",
            "live_txt": _txt_num(live["tpd"]),
            "limit_txt": _txt_num(limit["tpd"]),
            "bt_txt": f"{bt['trades_per_day']:.1f}",
            "verdict": _verdict_rate(ref["tpd"], bt["trades_per_day"]),
        },
        {
            "metric": "PF (lệnh đóng)",
            "live_txt": _txt_num(live["pf"], 2),
            "limit_txt": _txt_num(limit["pf"], 2),
            "bt_txt": f"~{bt['pf']:.2f}",
            "verdict": _verdict_pf(ref["pf"], bt["pf"]),
        },
        {
            "metric": "%/ngày total",
            "live_txt": _txt_pct(live["pct_day"], signed=True),
            "limit_txt": _txt_pct(limit["pct_day"], signed=True),
            "bt_txt": f"+{bt['pct_per_day']:.2f}%",
            "verdict": _verdict_pct_day(ref["pct_day"], bt["pct_per_day"]),
        },
        {
            "metric": "MaxDD futures",
            "live_txt": _txt_pct(live["maxdd_fut"]),
            "limit_txt": _txt_pct(limit["maxdd_fut"]),
            "bt_txt": f"~{bt['maxdd_futures_pct']:.0f}%",
            "verdict": _verdict_dd(ref["maxdd_fut"], bt["maxdd_futures_pct"]),
        },
        {
            "metric": "MaxDD total",
            "live_txt": _txt_pct(live["maxdd_tot"]),
            "limit_txt": _txt_pct(limit["maxdd_tot"]),
            "bt_txt": f"~{bt['maxdd_total_pct']:.0f}%",
            "verdict": _verdict_dd(ref["maxdd_tot"], bt["maxdd_total_pct"]),
        },
        {
            "metric": "Total vs vốn gốc",
            "live_txt": (
                f"{live['total_wealth']:.0f} / {principal:.0f} ({live['vs_principal_pct']:+.1f}%)"
                if live["vs_principal_pct"] is not None
                else "—"
            ),
            "limit_txt": (
                f"{limit['total_wealth']:.0f} / {limit['wealth_start']:.0f} "
                f"({limit['total_ret_pct']:+.1f}% · {limit['live_days']}d)"
                if limit["total_ret_pct"] is not None
                else "—"
            ),
            "bt_txt": "compound mạnh / năm",
            "verdict": _verdict_total(
                limit["total_ret_pct"] if limit["total_ret_pct"] is not None else live["vs_principal_pct"]
            ),
        },
    ]

    live_days = live["live_days"]
    limit_days = limit["live_days"]
    return {
        "bt_label": bt["label"],
        "live_days": live_days,
        "limit_days": limit_days,
        "live_label": f"Live (~{live_days}d)" if live_days else "Live",
        "limit_label": f"Từ limit (~{limit_days}d)" if limit_days else "Từ limit",
        "limit_since": LIMIT_TP_SINCE,
        "first_equity": live["first_eq"],
        "last_equity": live["last_eq"],
        "total_wealth": live["total_wealth"],
        "principal": principal,
        "total_ret_pct": live["total_ret_pct"],
        "rows": rows,
    }
