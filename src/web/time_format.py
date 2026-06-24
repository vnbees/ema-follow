from datetime import datetime, timezone
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def format_vn_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=VN_TZ).strftime("%d/%m/%Y %H:%M:%S")


def format_vn_now() -> str:
    return datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M:%S")


def format_vn_time(value: str | None) -> str:
    if not value:
        return "—"
    text = value.strip()
    if text.endswith(" UTC"):
        text = text[:-4].strip()
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return value
    else:
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return value
    return dt.astimezone(VN_TZ).strftime("%d/%m/%Y %H:%M:%S")
