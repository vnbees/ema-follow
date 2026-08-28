export type SignalStatus = "open" | "closed";

export type Signal = {
  id: number;
  external_id: string;
  symbol: string;
  side: "long" | "short" | string;
  trend: string | null;
  status: SignalStatus | string;
  entry: number;
  tp: number | null;
  close_price: number | null;
  equity_pct: number;
  pnl_pct: number | null;
  close_reason: string | null;
  opened_at: string;
  closed_at: string | null;
};

export type SignalListResponse = {
  status: string;
  page: number;
  page_size: number;
  total: number;
  pages: number;
  open_count: number;
  closed_count: number;
  signals: Signal[];
};

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

export async function fetchSignals(
  status: "open" | "closed" | "all" = "open",
  page = 1,
  pageSize = 50
): Promise<SignalListResponse> {
  const url = `${API_URL}/api/v1/signals?status=${status}&page=${page}&page_size=${pageSize}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${res.status}`);
  }
  return res.json();
}
