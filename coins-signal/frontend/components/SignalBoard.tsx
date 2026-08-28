"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { fetchSignals, type Signal, type SignalListResponse } from "@/lib/api";
import { fetchMarkPrices, livePnlPct, type MarkMap } from "@/lib/marks";
import { SignalRow } from "@/components/SignalRow";

type Tab = "open" | "closed";

const POLL_MS = 5000;
const MARK_MS = 3000;

export function SignalBoard() {
  const [tab, setTab] = useState<Tab>("open");
  const [data, setData] = useState<SignalListResponse | null>(null);
  const [marks, setMarks] = useState<MarkMap>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await fetchSignals(tab, 1, 50);
      setData(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Load failed");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    setLoading(true);
    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const openSymbols = useMemo(() => {
    if (!data || tab !== "open") return [] as string[];
    return data.signals.map((s) => s.symbol);
  }, [data, tab]);

  const openSymbolsKey = openSymbols.slice().sort().join(",");

  useEffect(() => {
    if (tab !== "open" || openSymbols.length === 0) return;

    let cancelled = false;
    const tick = async () => {
      const next = await fetchMarkPrices(openSymbols);
      if (!cancelled) setMarks(next);
    };
    void tick();
    const id = window.setInterval(() => void tick(), MARK_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- openSymbolsKey tracks symbol set
  }, [tab, openSymbolsKey]);

  const signals: Signal[] = data?.signals ?? [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-lg border border-white/10 bg-ink-900 p-1">
          {(
            [
              ["open", "Đang mở", data?.open_count],
              ["closed", "Đã đóng", data?.closed_count],
            ] as const
          ).map(([key, label, count]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={clsx(
                "rounded-md px-4 py-2 text-sm font-medium transition",
                tab === key
                  ? "bg-ink-700 text-white shadow"
                  : "text-slate-400 hover:text-slate-200"
              )}
            >
              {label}
              {typeof count === "number" && (
                <span className="ml-2 font-mono text-xs text-slate-400">{count}</span>
              )}
            </button>
          ))}
        </div>
        {loading && <span className="text-xs text-slate-500">Đang tải…</span>}
        {error && (
          <span className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-1 text-xs text-rose-300">
            {error}
          </span>
        )}
      </div>

      <div className="overflow-hidden rounded-xl border border-white/10 bg-ink-900/80">
        <div className="hidden grid-cols-12 gap-2 border-b border-white/10 px-4 py-2 text-[11px] uppercase tracking-wider text-slate-500 sm:grid">
          <div className="col-span-3">Symbol</div>
          <div className="col-span-2">Size</div>
          <div className="col-span-2">Entry / TP</div>
          <div className="col-span-2">{tab === "open" ? "Mark / Δ%" : "Exit / PnL%"}</div>
          <div className="col-span-3 text-right">{tab === "open" ? "Age" : "Closed"}</div>
        </div>

        {signals.length === 0 && !loading ? (
          <div className="px-4 py-16 text-center text-sm text-slate-500">
            Chưa có tín hiệu {tab === "open" ? "đang mở" : "đã đóng"}.
          </div>
        ) : (
          <ul className="divide-y divide-white/5">
            {signals.map((sig) => (
              <SignalRow
                key={sig.external_id}
                signal={sig}
                mark={marks[sig.symbol.toUpperCase()]}
                livePct={
                  tab === "open"
                    ? livePnlPct(sig.side, sig.entry, marks[sig.symbol.toUpperCase()])
                    : null
                }
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
