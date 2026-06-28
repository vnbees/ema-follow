import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
WEEKDAYS_VN = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def utc_timestamp_to_vn_date(timestamp: str) -> date:
    text = timestamp.strip()
    if text.endswith(" UTC"):
        text = text[:-4].strip()
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ).date()


def taken_at_to_vn_date(taken_at: str) -> date:
    return utc_timestamp_to_vn_date(taken_at)


def _month_nav(year: int, month: int) -> dict:
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    return {
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }


def _summarize_day_events(events: list[dict]) -> dict:
    daily_pnl = 0.0
    pnl_count = 0
    for event in events:
        pnl = event.get("realized_pnl_usdt")
        if pnl is not None:
            daily_pnl += float(pnl)
            pnl_count += 1
    return {
        "daily_pnl": daily_pnl if pnl_count else None,
        "trade_count": len(events),
        "pnl_count": pnl_count,
    }


def build_profit_calendar(year: int, month: int, events: list) -> dict:
    by_day: dict[int, list[dict]] = {}
    month_events: list[dict] = []

    for row in events:
        event = dict(row)
        vn_date = utc_timestamp_to_vn_date(str(event["taken_at"]))
        event["vn_date"] = vn_date.isoformat()
        if vn_date.year == year and vn_date.month == month:
            by_day.setdefault(vn_date.day, []).append(event)
            month_events.append(event)

    month_events.sort(key=lambda e: e["taken_at"], reverse=True)

    cal = calendar.Calendar(firstweekday=0)
    weeks: list[list[dict]] = []
    for week in cal.monthdatescalendar(year, month):
        row = []
        for d in week:
            day_events = by_day.get(d.day, []) if d.month == month else []
            last_event = day_events[-1] if day_events else None
            row.append(
                {
                    "day": d.day if d.month == month else "",
                    "in_month": d.month == month,
                    "marked": bool(day_events),
                    "count": len(day_events),
                    "is_today": d == datetime.now(VN_TZ).date(),
                    "equity_after": float(last_event["equity_after"]) if last_event else None,
                    "events": day_events,
                }
            )
        weeks.append(row)

    return {
        "year": year,
        "month": month,
        "month_label": f"{month:02d}/{year}",
        "weekdays": WEEKDAYS_VN,
        "weeks": weeks,
        "month_events": month_events,
        **_month_nav(year, month),
    }


def build_rsi_pnl_calendar(year: int, month: int, trades: list) -> dict:
    by_day: dict[int, list[dict]] = {}
    month_events: list[dict] = []
    total_pnl = 0.0
    total_trades = 0
    total_with_pnl = 0

    for row in trades:
        event = dict(row)
        vn_date = utc_timestamp_to_vn_date(str(event["closed_at"]))
        event["vn_date"] = vn_date.isoformat()
        if vn_date.year != year or vn_date.month != month:
            continue
        by_day.setdefault(vn_date.day, []).append(event)
        month_events.append(event)
        total_trades += 1
        pnl = event.get("realized_pnl_usdt")
        if pnl is not None:
            total_pnl += float(pnl)
            total_with_pnl += 1

    month_events.sort(key=lambda e: e["closed_at"], reverse=True)

    cal = calendar.Calendar(firstweekday=0)
    weeks: list[list[dict]] = []
    for week in cal.monthdatescalendar(year, month):
        row = []
        for d in week:
            if d.month != month:
                row.append(
                    {
                        "day": "",
                        "in_month": False,
                        "marked": False,
                        "daily_pnl": None,
                        "trade_count": 0,
                        "pnl_count": 0,
                        "is_today": False,
                        "events": [],
                    }
                )
                continue

            day_events = by_day.get(d.day, [])
            summary = _summarize_day_events(day_events)
            row.append(
                {
                    "day": d.day,
                    "in_month": True,
                    "marked": bool(day_events),
                    "is_today": d == datetime.now(VN_TZ).date(),
                    "events": day_events,
                    **summary,
                }
            )
        weeks.append(row)

    return {
        "year": year,
        "month": month,
        "month_label": f"{month:02d}/{year}",
        "weekdays": WEEKDAYS_VN,
        "weeks": weeks,
        "month_events": month_events,
        "month_summary": {
            "total_pnl": total_pnl if total_with_pnl else None,
            "total_trades": total_trades,
            "total_with_pnl": total_with_pnl,
        },
        **_month_nav(year, month),
    }
