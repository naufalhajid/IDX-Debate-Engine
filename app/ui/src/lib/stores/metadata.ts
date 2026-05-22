import { writable, derived } from 'svelte/store';

export interface StockMeta {
  ticker: string;
  name: string;
  market_cap: number | null;
  home_page: string | null;
}

/** Raw list of all stocks loaded from the API */
export const stockList = writable<StockMeta[]>([]);

/** Fast lookup map: ticker → StockMeta */
export const stockMap = derived(stockList, ($list) => {
  const map = new Map<string, StockMeta>();
  for (const s of $list) {
    map.set(s.ticker, s);
  }
  return map;
});

/** Sector cache loaded from the API results */
export const sectorCache = writable<Record<string, { sector?: string; yf_sector?: string; yf_industry?: string }>>({});

const SECTOR_LABELS: Record<string, string> = {
  bank: 'Banking',
  finance_nonbank: 'Non-Bank Finance',
  consumer_staples: 'Consumer Staples',
  consumer_disc: 'Consumer Discretionary',
  energy: 'Energy',
  basic_materials: 'Basic Materials',
  industrials: 'Industrials',
  property: 'Property & Real Estate',
  tech: 'Technology',
  healthcare: 'Healthcare',
  infrastructure: 'Infrastructure',
  transport: 'Transportation',
  utilities: 'Utilities',
  unknown: 'IDX Listed'
};

/**
 * Convert sector slug like "consumer_staples" into "Consumer Staples".
 * Falls back to title-casing the slug if not in the known map.
 */
export function formatSector(slug: string | null | undefined): string {
  if (!slug) return 'IDX Listed';
  const lower = slug.toLowerCase().trim();
  if (SECTOR_LABELS[lower]) return SECTOR_LABELS[lower];
  // Generic title-case fallback
  return lower
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Resolve a human-readable company name for a ticker.
 * Uses the stockMap store value (pass $stockMap).
 */
export function resolveCompanyName(
  map: Map<string, StockMeta>,
  ticker: string,
  fallbackSector?: string
): string {
  const meta = map.get(ticker);
  if (meta?.name) return meta.name;
  if (fallbackSector && fallbackSector !== 'unknown' && fallbackSector !== 'Unknown') {
    return formatSector(fallbackSector);
  }
  return 'IDX Listed';
}
