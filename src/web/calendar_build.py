import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
WEEKDAYS_VN = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def taken_at_to_vn_date(taken_at: str) -> date:
    text = taken_at.strip()
    if text.endswith(" UTC"):
        text = text[:-4].strip()
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ).date()


def build_profit_calendar(year: int, month: int, events: list) -> dict:
    by_day: dict[int, list[dict]] = {}
    month_events: list[dict] = []

    for row in events:
        event = dict(row)
        vn_date = taken_at_to_vn_date(str(event["taken_at"]))
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

    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    return {
        "year": year,
        "month": month,
        "month_label": f"{month:02d}/{year}",
        "weekdays": WEEKDAYS_VN,
        "weeks": weeks,
        "month_events": month_events,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }
