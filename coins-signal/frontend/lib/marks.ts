/** Fetch Binance USDT-M mark prices for open-signal ROI (public, no auth). */

const MARK_URL = "https://fapi.binance.com/fapi/v1/premiumIndex";

export type MarkMap = Record<string, number>;

export async function fetchMarkPrices(symbols: string[]): Promise<MarkMap> {
  const unique = Array.from(
    new Set(symbols.map((s) => s.toUpperCase()).filter(Boolean))
  );
  if (unique.length === 0) return {};

  try {
    const res = await fetch(MARK_URL, { cache: "no-store" });
    if (!res.ok) return {};
    const data = (await res.json()) as Array<{ symbol: string; markPrice: string }>;
    const wanted = new Set(unique);
    const out: MarkMap = {};
    for (const row of data) {
      if (wanted.has(row.symbol)) {
        const px = Number(row.markPrice);
        if (Number.isFinite(px) && px > 0) out[row.symbol] = px;
      }
    }
    return out;
  } catch {
    return {};
  }
}

/** Unrealized ROI % on margin ≈ price move % × leverage assumption is NOT used.
 *  We show price move % vs entry (direction-aware) as live estimate — not account ROI.
 *  For display: long (mark-entry)/entry*100, short (entry-mark)/entry*100.
 */
export function livePnlPct(side: string, entry: number, mark: number | undefined): number | null {
  if (!mark || !entry || entry <= 0) return null;
  const s = side.toLowerCase();
  if (s === "long") return ((mark - entry) / entry) * 100;
  if (s === "short") return ((entry - mark) / entry) * 100;
  return null;
}
