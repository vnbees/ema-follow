"""Discord webhook notifications (close + errors). Fail-soft — never breaks trading."""

from __future__ import annotations

import logging
import threading
import time

import requests

from src.config import (
    DEFAULT_SYMBOL,
    DISCORD_WEBHOOK_URL,
    MARGIN_COIN,
)
from src.exchange import ExchangeClientError, fetch_futures_balance, has_credentials

# Same error context: at most one Discord ping per cooldown window.
_ERROR_NOTIFY_COOLDOWN_SEC = 180.0
_last_error_notify_at: dict[str, float] = {}


def discord_configured() -> bool:
    return bool(DISCORD_WEBHOOK_URL)


def _format_balance_body() -> str:
    if not has_credentials():
        return f"Futures balance: unavailable ({MARGIN_COIN})"
    try:
        balance = fetch_futures_balance(DEFAULT_SYMBOL)
        return (
            f"Futures balance: available={balance.available:.2f} {balance.margin_coin}"
            f" | equity={balance.account_equity:.2f} {balance.margin_coin}"
            f" | maint={balance.maint_margin_pct:.2f}%"
            f" | initial={balance.initial_margin_pct:.2f}%"
        )
    except ExchangeClientError as exc:
        logging.warning("Discord notify: balance fetch failed (%s)", exc)
        return f"Futures balance: fetch failed ({MARGIN_COIN})"


def _send_discord(title: str, body: str) -> None:
    content = f"**{title}**\n{body}"
    if len(content) > 2000:
        content = content[:1997] + "..."
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": content},
        timeout=10,
    )
    if not response.ok:
        raise RuntimeError(
            f"Discord webhook HTTP {response.status_code}: {response.text[:200]}"
        )


def _fmt_px(value: float) -> str:
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _hold_label(opened_at: str) -> str:
    raw = (opened_at or "").strip()
    if not raw:
        return "—"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
    except ValueError:
        return "—"
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def notify_smc_fill(
    symbol: str,
    side: str,
    *,
    entry: float,
    stop: float,
    tp: float,
    qty: float,
    margin_usdt: float,
) -> None:
    """Discord when a reversed SMC limit fills. Fail-soft."""
    try:
        if not discord_configured():
            return
        title = f"{symbol.upper()} {side.upper()} khớp"
        body = (
            f"entry={_fmt_px(entry)}\n"
            f"cắt lỗ={_fmt_px(stop)}  chốt={_fmt_px(tp)}\n"
            f"qty={qty:g}  margin={margin_usdt:.2f} USDT"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_smc_fill failed: %s", exc)


def notify_smc_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    entry: float,
    stop: float,
    tp: float,
    close_px: float,
    pnl_usdt: float,
    qty: float,
    margin_usdt: float,
    opened_at: str = "",
) -> None:
    """Discord when a reversed SMC position closes. Fail-soft."""
    try:
        if not discord_configured():
            return
        label = {"tp": "chốt lời", "stop": "cắt lỗ", "flat": "đóng tay", "time": "hết 41 phút"}.get(reason, reason)
        title = f"{symbol.upper()} {side.upper()} đóng — {label}"
        body = (
            f"entry={_fmt_px(entry)}  cắt lỗ={_fmt_px(stop)}  chốt={_fmt_px(tp)}\n"
            f"qty={qty:g}  margin={margin_usdt:.2f} USDT\n"
            f"đóng={_fmt_px(close_px)}\n"
            f"pnl={pnl_usdt:+.2f} USDT (đã trừ phí)\n"
            f"giữ {_hold_label(opened_at)}"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_smc_close failed: %s", exc)


def notify_rsi_rev_open(
    symbol: str,
    side: str,
    *,
    zone: str,
    anchor: float,
    entry: float,
    tp: float,
    size: float,
    margin_usdt: float,
) -> None:
    """Discord when an RSI-rev lot is opened. Fail-soft. No REST balance fetch."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return
        title = f"{symbol.upper()} {side.upper()} mở — {zone}"
        body = (
            f"anchor={_fmt_px(anchor)}\n"
            f"entry={_fmt_px(entry)}\n"
            f"target TP={_fmt_px(tp)}\n"
            f"size={size:g}  margin={margin_usdt:.2f} USDT"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_rsi_rev_open failed: %s", exc)


def notify_rsi_rev_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    zone: str,
    anchor: float,
    entry: float,
    tp: float,
    close_price: float,
    pnl_usdt: float,
    opened_at: str = "",
) -> None:
    """Discord when an RSI-rev lot is closed. Fail-soft. No REST balance fetch."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return
        title = f"{symbol.upper()} {side.upper()} đóng — {reason}"
        body = (
            f"vùng RSI: {zone}\n"
            f"anchor={_fmt_px(anchor)}  entry={_fmt_px(entry)}  target TP={_fmt_px(tp)}\n"
            f"đóng={_fmt_px(close_price)}\n"
            f"pnl={pnl_usdt:+.2f} USDT (đã trừ phí)\n"
            f"giữ {_hold_label(opened_at)}"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_rsi_rev_close failed: %s", exc)


_NOISY_LOGGERS = frozenset({"uvicorn", "uvicorn.error", "asyncio", "httpx", "urllib3", "websockets"})


class DiscordErrorLogHandler(logging.Handler):
    """Forward ERROR+ logs to Discord. Fail-soft; cooldown per logger name."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        root = (record.name or "root").split(".", 1)[0]
        if record.name in _NOISY_LOGGERS or root in _NOISY_LOGGERS:
            return
        if getattr(record, "skip_discord", False):
            return
        try:
            notify_error(record.name or "error", self.format(record))
        except Exception:  # noqa: BLE001
            pass


def notify_spot_transfer(
    *,
    transfer_date: str,
    status: str,
    amount: float,
    detail: str,
    day_pnl: float = 0.0,
    dd_pct: float = 0.0,
    equity: float = 0.0,
    peak: float = 0.0,
) -> None:
    """Discord daily futures→spot decision (success / skipped / failed). Fail-soft."""
    try:
        if not discord_configured():
            return
        title = f"Spot transfer {transfer_date} — {status}"
        body = (
            f"{detail}\n"
            f"amount={amount:.2f} {MARGIN_COIN} | day_pnl={day_pnl:+.2f} | "
            f"DD={dd_pct*100:.1f}% | equity={equity:.2f} | peak={peak:.2f}"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_spot_transfer failed: %s", exc)


def notify_risk_warning(kind: str, message: str) -> None:
    """Discord risk warn (DD / maint / initial). Fail-soft."""
    try:
        if not discord_configured():
            return
        _send_discord(f"Risk warn — {kind}", message)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_risk_warning failed: %s", exc)


def notify_predict5m_open(
    symbol: str,
    side: str,
    *,
    stake: float,
    step: int = 0,
    window_start_ms: int,
    dry_run: bool = False,
    limit_px: float | None = None,
    interval: str = "5m",
    target: float | None = None,
) -> None:
    try:
        if not discord_configured():
            return
        from datetime import datetime, timedelta, timezone

        vn = timezone(timedelta(hours=7))
        win = datetime.fromtimestamp(int(window_start_ms) / 1000, vn).strftime("%H:%M")
        tag = " dry-run" if dry_run else ""
        px = f" LIMIT {limit_px:.2f} (lãi ≥2× vốn)" if limit_px else ""
        tgt = "" if target is None else f"target={target:.2f} → "
        body = (
            f"{tgt}cược={stake:.2f} USDT{px}\n"
            f"cửa {interval} {win} +07{tag}"
        )
        _send_discord(
            f"{symbol.upper()} {side.upper()} mở — Prediction {interval}", body
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_predict5m_open failed: %s", exc)


def notify_predict5m_result(
    symbol: str,
    side: str,
    *,
    won: bool | None,
    filled: bool,
    stake: float,
    pnl: float,
    reason: str,
    step: int | None = None,
    interval: str = "5m",
    target: float | None = None,
    next_target: float | None = None,
    next_stake: float | None = None,
) -> None:
    try:
        if not discord_configured():
            return
        if not filled:
            title = f"{symbol.upper()} {side.upper()} không khớp — Prediction {interval}"
            body = f"LIMIT hết cửa, không đổi target\ncược={stake:.2f} USDT\n{reason}"
        elif won:
            title = f"{symbol.upper()} {side.upper()} thắng — Prediction {interval}"
            rst = f"{next_target:.0f}" if next_target else "2"
            body = f"pnl=+{pnl:.2f} USDT\ncược={stake:.2f} reset target về {rst}"
        else:
            nxt = ""
            if next_target is not None and next_stake is not None:
                nxt = f"\ntarget tiếp {next_target:.2f} → cược {next_stake:.2f} USDT"
            elif step is not None:
                nxt = f"\nbước tiếp {int(step) + 1}: {stake * 2:.0f} USDT"
            title = f"{symbol.upper()} {side.upper()} thua — Prediction {interval}"
            body = f"pnl={pnl:.2f} USDT\ncược={stake:.2f}{nxt}"
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_predict5m_result failed: %s", exc)


def notify_predict5m_pause(
    *,
    hours: float = 1.0,
    until_ms: int | None = None,
    stake: float | None = None,
    available: float | None = None,
    interval: str = "5m",
) -> None:
    try:
        if not discord_configured():
            return
        extra = ""
        if until_ms:
            from datetime import datetime, timedelta, timezone

            vn = timezone(timedelta(hours=7))
            extra = f"\nchạy lại {datetime.fromtimestamp(int(until_ms) / 1000, vn).strftime('%H:%M')} +07"
        wallet = ""
        if stake is not None and available is not None:
            wallet = f"cần {stake:.0f} USDT, ví còn {available:.2f}\n"
        body = (
            f"ví không đủ lệnh tiếp theo, reset target\n"
            f"{wallet}"
            f"dừng {hours:g}h rồi mới vào lệnh{extra}"
        )
        _send_discord(f"Prediction {interval} dừng 1h", body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_predict5m_pause failed: %s", exc)


def notify_predict5m_resume(*, stake: float = 1.0, interval: str = "5m") -> None:
    try:
        if not discord_configured():
            return
        body = f"hết giờ dừng 1h\ncược lại {stake:.0f} USDT"
        _send_discord(f"Prediction {interval} chạy lại", body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_predict5m_resume failed: %s", exc)


def notify_trend4h_open(symbol: str, side: str, *, entry: float, bar_ts: int) -> None:
    try:
        if not discord_configured():
            return
        body = f"vào={_fmt_px(entry)}\nSupertrend 4h + ADX≥25\nký quỹ=1 USDT × 10"
        _send_discord(f"{symbol.upper()} {side.upper()} mở — trend 4h", body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_trend4h_open failed: %s", exc)


def notify_fade5m_limit(symbol: str, side: str, *, px: float, bar_ts: int, qty: float | None = None) -> None:
    try:
        if not discord_configured():
            return
        qty_txt = f"qty={qty}\n" if qty is not None else ""
        body = f"limit={_fmt_px(px)}\n{qty_txt}4h màu ngược nến 5m"
        _send_discord(f"{symbol.upper()} {side.upper()} limit — fade 5m", body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_fade5m_limit failed: %s", exc)


def notify_fade5m_fill(symbol: str, side: str, *, entry: float, bar_ts: int) -> None:
    try:
        if not discord_configured():
            return
        body = f"khớp={_fmt_px(entry)}\nbar_ts={bar_ts}"
        _send_discord(f"{symbol.upper()} {side.upper()} khớp — fade 5m", body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_fade5m_fill failed: %s", exc)


def notify_fade5m_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    entry: float,
    exit_px: float,
    pnl: float,
) -> None:
    try:
        if not discord_configured():
            return
        body = f"vào={_fmt_px(entry)}  đóng={_fmt_px(exit_px)}\npnl={pnl:+.4f} USDT\n{reason}"
        _send_discord(f"{symbol.upper()} {side.upper()} đóng — fade 5m", body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_fade5m_close failed: %s", exc)


def notify_trend4h_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    entry: float,
    exit_px: float,
    pnl: float,
) -> None:
    try:
        if not discord_configured():
            return
        label = "đảo Supertrend" if reason == "st_flip" else reason
        body = f"vào={_fmt_px(entry)}  đóng={_fmt_px(exit_px)}\npnl={pnl:+.4f} USDT"
        _send_discord(f"{symbol.upper()} {side.upper()} đóng — {label}", body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_trend4h_close failed: %s", exc)


def notify_revert_open(
    symbol: str,
    side: str,
    *,
    level: float,
    entry: float,
    tp: float,
    margin_usdt: float,
) -> None:
    """Discord when a return-to-signal limit is placed. Fail-soft."""
    try:
        if not discord_configured():
            return
        title = f"{symbol.upper()} {side.upper()} mở"
        body = (
            f"mức tín hiệu={_fmt_px(level)}\n"
            f"limit vào={_fmt_px(entry)}  chốt={_fmt_px(tp)}\n"
            f"ký quỹ={margin_usdt:.2f} USDT × 10"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_revert_open failed: %s", exc)


def notify_revert_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    level: float,
    entry: float,
    close_px: float | None,
    pnl_usdt: float | None,
) -> None:
    """Discord when a return-to-signal order closes or is cancelled. Fail-soft."""
    try:
        if not discord_configured():
            return
        label = {"tp": "chốt lời", "expire": "hết 7 ngày", "cancel": "hủy, chưa khớp"}.get(reason, reason)
        title = f"{symbol.upper()} {side.upper()} đóng — {label}"
        pnl = "chưa khớp" if pnl_usdt is None else f"{pnl_usdt:+.4f} USDT"
        body = (
            f"mức tín hiệu={_fmt_px(level)}\n"
            f"vào={_fmt_px(entry)}  đóng={_fmt_px(close_px) if close_px else '—'}\n"
            f"pnl={pnl}"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_revert_close failed: %s", exc)


def notify_error(context: str, detail: str, *, cooldown_sec: float | None = None) -> None:
    """Send bot-error notification to Discord. Never raises; no balance fetch (avoid API during bans)."""
    try:
        if not discord_configured():
            logging.debug("Discord notify skipped: DISCORD_WEBHOOK_URL not set")
            return

        window = _ERROR_NOTIFY_COOLDOWN_SEC if cooldown_sec is None else max(0.0, cooldown_sec)
        key = context.strip() or "error"
        now = time.monotonic()
        last = _last_error_notify_at.get(key, 0.0)
        if window > 0 and (now - last) < window:
            logging.debug("Discord error notify cooldown: %s", key)
            return
        _last_error_notify_at[key] = now

        title = f"Bot lỗi: {key}"
        body = str(detail).strip() or "(no detail)"
        try:
            _send_discord(title, body)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logging.warning("Discord notify send failed: %s", exc, extra={"skip_discord": True})
    except Exception as extra_exc:  # noqa: BLE001 — never break trading
        logging.warning("Discord notify_error failed: %s", extra_exc, extra={"skip_discord": True})


def install_error_hooks() -> None:
    """Discord on uncaught exceptions (main + threads) and ERROR logs."""
    import sys
    import traceback

    root = logging.getLogger()
    if not any(isinstance(h, DiscordErrorLogHandler) for h in root.handlers):
        handler = DiscordErrorLogHandler(level=logging.ERROR)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)

    def _hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc, tb))[-1500:]
        notify_error("uncaught", f"{exc_type.__name__}: {exc}\n{detail}", cooldown_sec=30)
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args) -> None:
        if args.exc_type is None or issubclass(args.exc_type, KeyboardInterrupt):
            return
        name = args.thread.name if args.thread else "thread"
        detail = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )[-1500:]
        notify_error(
            f"thread {name}",
            f"{args.exc_type.__name__}: {args.exc_value}\n{detail}",
            cooldown_sec=30,
        )

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
