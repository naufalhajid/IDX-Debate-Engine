import { derived, writable } from 'svelte/store';
import type { DebateEvent, StockResult } from '$lib/types';

export const allResults = writable<StockResult[]>([]);
export const activeTicker = writable<string | null>(null);
export const debateStream = writable<DebateEvent[]>([]);
export const isStreaming = writable<boolean>(false);
export const searchQuery = writable<string>('');

export const filteredResults = derived(
  [allResults, searchQuery],
  ([$results, $query]) => {
    const query = $query.trim().toUpperCase();
    if (!query) return $results;
    return $results.filter(
      (result) =>
        result.ticker.includes(query) ||
        (result.sector ?? '').toUpperCase().includes(query)
    );
  }
);

export const summaryStats = derived(allResults, ($results) => ({
  total: $results.length,
  strongBuy: $results.filter((result) => result.rating === 'STRONG_BUY').length,
  buy: $results.filter((result) => result.rating === 'BUY').length,
  avoid: $results.filter((result) => result.rating === 'AVOID').length,
  avgConviction: $results.length
    ? Math.round(
        $results.reduce((total, result) => total + result.conviction_score, 0) /
          $results.length
      )
    : 0
}));
