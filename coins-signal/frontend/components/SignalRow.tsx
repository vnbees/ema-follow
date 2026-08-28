"use client";

import clsx from "clsx";
import type { Signal } from "@/lib/api";
import {
  closeReasonLabel,
  displaySymbol,
  formatAge,
  formatEquityPct,
  formatPct,
  formatPrice,
  formatVnTime,
} from "@/lib/format";

type Props = {
  signal: Signal;
  mark?: number;
  livePct: number | null;
};

export function SignalRow({ signal, mark, livePct }: Props) {
  const isLong = signal.side.toLowerCase() === "long";
  const isOpen = signal.status === "open";
  const pnl = isOpen ? livePct : signal.pnl_pct;
  const pnlPositive = pnl != null && pnl >= 0;

  return (
    <li className="grid grid-cols-1 gap-3 px-4 py-4 sm:grid-cols-12 sm:items-center sm:gap-2">
      <div className="col-span-3 flex items-center gap-3">
        <span
          className={clsx(
            "rounded px-2 py-0.5 font-mono text-[11px] font-semibold uppercase",
            isLong ? "bg-long/15 text-long" : "bg-short/15 text-short"
          )}
        >
          {isLong ? "LONG" : "SHORT"}
        </span>
        <div>
          <div className="font-semibold tracking-tight">{displaySymbol(signal.symbol)}</div>
          <div className="font-mono text-[11px] text-slate-500">{signal.symbol}</div>
        </div>
      </div>

      <div className="col-span-2">
        <div className="font-mono text-sm text-accent">{formatEquityPct(signal.equity_pct)}</div>
        <div className="text-[11px] text-slate-500">% equity</div>
      </div>

      <div className="col-span-2 font-mono text-sm">
        <div>{formatPrice(signal.entry)}</div>
        <div className="text-[11px] text-slate-500">TP {formatPrice(signal.tp)}</div>
      </div>

      <div className="col-span-2 font-mono text-sm">
        {isOpen ? (
          <>
            <div>{formatPrice(mark)}</div>
            <div
              className={clsx(
                "text-[11px]",
                pnl == null ? "text-slate-500" : pnlPositive ? "text-long" : "text-short"
              )}
            >
              {formatPct(pnl)}
            </div>
          </>
        ) : (
          <>
            <div>{formatPrice(signal.close_price)}</div>
            <div
              className={clsx(
                "text-[11px]",
                pnl == null ? "text-slate-500" : pnlPositive ? "text-long" : "text-short"
              )}
            >
              {formatPct(signal.pnl_pct)} · {closeReasonLabel(signal.close_reason)}
            </div>
          </>
        )}
      </div>

      <div className="col-span-3 text-left sm:text-right">
        {isOpen ? (
          <>
            <div className="font-mono text-sm">{formatAge(signal.opened_at)}</div>
            <div className="text-[11px] text-slate-500">{formatVnTime(signal.opened_at)}</div>
          </>
        ) : (
          <>
            <div className="font-mono text-sm">{formatVnTime(signal.closed_at)}</div>
            <div className="text-[11px] text-slate-500">mở {formatVnTime(signal.opened_at)}</div>
          </>
        )}
      </div>
    </li>
  );
}
