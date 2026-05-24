<script lang="ts">
  import { onMount } from 'svelte';
  import { activeTicker, filteredResults, isStreaming, searchQuery, activeTab } from '$lib/stores/dashboard';
  import { stockMap, resolveCompanyName } from '$lib/stores/metadata';
  import type { Rating, StockResult } from '$lib/types';

  export let onDebate: (tickers: string[]) => void = () => {};

  const rowHeight = 64;
  const buffer = 5;
  const RATING_CONFIG: Record<Rating, { label: string; color: string }> = {
    STRONG_BUY: { label: 'STRONG BUY', color: 'var(--signal-bull)' },
    BUY: { label: 'BUY', color: '#34d399' },
    HOLD: { label: 'HOLD', color: 'var(--signal-hold)' },
    AVOID: { label: 'AVOID', color: 'var(--signal-bear)' }
  };
  const RATING_RANK: Record<Rating, number> = {
    STRONG_BUY: 4,
    BUY: 3,
    HOLD: 2,
    AVOID: 1
  };

  let selectedTickers = new Set<string>();
  let sortKey: keyof StockResult = 'conviction_score';
  let sortDir: 'desc' | 'asc' = 'desc';
  let scrollTop = 0;
  let scrollLeft = 0;
  let viewportHeight = 600;
  let bodyEl: HTMLDivElement | undefined;

  function isChecked(event: Event) {
    return (event.currentTarget as HTMLInputElement).checked;
  }

  function compare(a: StockResult, b: StockResult, key: keyof StockResult, dir: 'desc' | 'asc') {
    if (key === 'rating') {
      const ratingA = (a.rating || '').toUpperCase() as Rating;
      const ratingB = (b.rating || '').toUpperCase() as Rating;
      const ra = RATING_RANK[ratingA] ?? 0;
      const rb = RATING_RANK[ratingB] ?? 0;
      return dir === 'desc' ? rb - ra : ra - rb;
    }
    const va = a[key] ?? '';
    const vb = b[key] ?? '';
    if (typeof va === 'number' && typeof vb === 'number') {
      return dir === 'desc' ? vb - va : va - vb;
    }
    return dir === 'desc'
      ? String(vb).localeCompare(String(va))
      : String(va).localeCompare(String(vb));
  }

  function sort(key: keyof StockResult) {
    if (sortKey === key) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    else {
      sortKey = key;
      sortDir = 'desc';
    }
  }

  function toggleTicker(ticker: string, checked: boolean) {
    const next = new Set(selectedTickers);
    if (checked) next.add(ticker);
    else next.delete(ticker);
    selectedTickers = next;
  }

  function toggleAll(checked: boolean) {
    selectedTickers = checked ? new Set(sorted.map((stock) => stock.ticker)) : new Set();
  }

  function formatPrice(value: number) {
    return value.toLocaleString('id-ID');
  }

  function formatEntry(stock: StockResult) {
    return `${formatPrice(stock.entry_low)} - ${formatPrice(stock.entry_high)}`;
  }

  function formatDate(val: string) {
    if (!val) return '-';
    return val.split(' ')[0];
  }

  function exportCSV() {
    const rows = sorted.map((result) =>
      [
        result.ticker,
        result.sector,
        result.conviction_score,
        result.rating,
        result.risk_reward,
        result.target_price,
        result.stop_loss,
        result.actionable ? 'actionable' : 'watch'
      ]
        .map((value) => `"${String(value).replaceAll('"', '""')}"`)
        .join(',')
    );
    const csv = [
      'Ticker,Sector,Conviction,Rating,RiskReward,Target,StopLoss,Status',
      ...rows
    ].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'idx-analysis.csv';
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function handleScroll(event: Event) {
    const target = event.currentTarget as HTMLDivElement;
    scrollTop = target.scrollTop;
    scrollLeft = target.scrollLeft;
    viewportHeight = target.clientHeight;
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    const current = sorted.findIndex((stock) => stock.ticker === $activeTicker);
    const fallback = event.key === 'ArrowDown' ? 0 : sorted.length - 1;
    const nextIndex =
      current === -1
        ? fallback
        : Math.max(0, Math.min(sorted.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)));
    const next = sorted[nextIndex];
    if (next) activeTicker.set(next.ticker);
  }

  onMount(() => {
    const listener = () => exportCSV();
    window.addEventListener('idx-export-csv', listener);
    if (bodyEl) viewportHeight = bodyEl.clientHeight;
    return () => window.removeEventListener('idx-export-csv', listener);
  });

  $: sorted = [...$filteredResults].sort((a, b) => compare(a, b, sortKey, sortDir));
  $: selectedCount = selectedTickers.size;
  $: allChecked = sorted.length > 0 && selectedCount === sorted.length;
  $: startIndex = Math.max(0, Math.floor(scrollTop / rowHeight) - buffer);
  $: visibleCount = Math.ceil(viewportHeight / rowHeight) + buffer * 2;
  $: endIndex = Math.min(sorted.length, startIndex + visibleCount);
  $: visibleRows = sorted.slice(startIndex, endIndex);
  $: offsetY = startIndex * rowHeight;
  $: totalHeight = sorted.length * rowHeight;
</script>

<div class="table-panel terminal-panel">
  <header class="panel-header">
    <div class="header-titles">
      <h2 class="panel-heading">{$activeTab === 'watchlist' ? 'Watchlist Saham' : 'Rekomendasi Saham'}</h2>
      <span class="panel-subtitle">
        {$activeTab === 'watchlist'
          ? 'Daftar saham potensial dengan rating STRONG BUY, BUY, dan HOLD.'
          : 'Daftar kandidat potensial berdasarkan analisis mesin fundamental.'}
      </span>
    </div>
    <div class="panel-actions">
      <div class="search-wrap">
        <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input
          class="search-input"
          placeholder="Cari Ticker / Sektor..."
          bind:value={$searchQuery}
        />
        {#if $searchQuery}
          <button class="clear-btn" onclick={() => searchQuery.set('')} type="button">✕</button>
        {/if}
      </div>
      {#if selectedCount > 0}
        <button
          class="btn btn--primary debate-btn"
          disabled={$isStreaming}
          onclick={() => onDebate([...selectedTickers])}
          type="button"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Mulai Debat ({selectedCount})
        </button>
      {/if}
      <button class="btn export-btn" onclick={exportCSV} title="Export CSV" type="button">
        Export CSV
      </button>
    </div>
  </header>

  <div class="table-head" style="transform: translateX(-{scrollLeft}px)">
    <label class="cell cell--check" title="Select all">
      <input type="checkbox" checked={allChecked} onchange={(event) => toggleAll(isChecked(event))} />
    </label>
    <button class="cell cell--ticker sortable" onclick={() => sort('ticker')} type="button">
      TICKER {sortKey === 'ticker' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </button>
    <div class="cell cell--company">PERUSAHAAN</div>
    <button class="cell cell--conv sortable" onclick={() => sort('conviction_score')} type="button">
      SKOR KEYAKINAN {sortKey === 'conviction_score' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </button>
    <button class="cell cell--rating sortable" onclick={() => sort('rating')} type="button">
      RATING {sortKey === 'rating' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </button>
    <button class="cell cell--price sortable" onclick={() => sort('entry_low')} type="button">
      HARGA (IDR) {sortKey === 'entry_low' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </button>
    <button class="cell cell--edge sortable" onclick={() => sort('risk_reward')} type="button">
      RISK/REWARD {sortKey === 'risk_reward' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </button>
    <div class="cell cell--entry">AREA ENTRY</div>
    <button class="cell cell--date sortable" onclick={() => sort('last_debated_at')} type="button">
      Latest{sortKey === 'last_debated_at' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
    </button>
  </div>

  <div
    class="table-body"
    bind:this={bodyEl}
    role="grid"
    aria-label="Candidates table"
    tabindex="0"
    onscroll={handleScroll}
    onkeydown={handleKeydown}
  >
    <div class="virtual-space" style="height: {totalHeight}px">
      <div class="virtual-window" style="transform: translateY({offsetY}px)">
        {#each visibleRows as stock (stock.ticker)}
          {@const cfg = RATING_CONFIG[stock.rating] ?? RATING_CONFIG.HOLD}
          {@const filledCount = Math.round((stock.conviction_score ?? 0) / 10)}
          {@const emptyCount = Math.max(0, 10 - filledCount)}
          <button
            class="table-row"
            class:table-row--active={$activeTicker === stock.ticker}
            style="height: {rowHeight}px"
            onclick={() => activeTicker.set(stock.ticker)}
            type="button"
          >
            <span class="cell cell--check">
              <input
                type="checkbox"
                checked={selectedTickers.has(stock.ticker)}
                onclick={(event) => event.stopPropagation()}
                onchange={(event) => toggleTicker(stock.ticker, isChecked(event))}
              />
            </span>
            <span class="cell cell--ticker ticker-code">
              <strong>{stock.ticker}</strong>
              <span
                class="quick-debate"
                class:quick-debate--disabled={$isStreaming}
                title="Debate {stock.ticker}"
                role="button"
                tabindex="0"
                onclick={(event) => { event.stopPropagation(); if (!$isStreaming) onDebate([stock.ticker]); }}
                onkeydown={(event) => { if (event.key === 'Enter') { event.stopPropagation(); if (!$isStreaming) onDebate([stock.ticker]); } }}
              >▶</span>
            </span>
            <span class="cell cell--company company-cell">
              {resolveCompanyName($stockMap, stock.ticker, stock.sector)}
            </span>
            <span class="cell cell--conv conviction-cell">
              <div class="conv-score">{stock.conviction_score}%</div>
              <div class="text-progress-bar" style="color: {cfg.color}">
                [{'■'.repeat(filledCount)}<span class="muted-blocks">{'□'.repeat(emptyCount)}</span>]
              </div>
            </span>
            <span class="cell cell--rating">
              <span class="badge" style="--badge-color: {cfg.color}">{cfg.label}</span>
            </span>
            <span class="cell cell--price mono">{formatPrice(stock.entry_low)}</span>
            <span
              class="cell cell--edge mono"
              class:cell--negative={stock.risk_reward < 1}
            >
              {stock.risk_reward >= 1 ? '+' : ''}{stock.risk_reward.toFixed(2)}x
            </span>
            <span class="cell cell--entry mono">{formatEntry(stock)}</span>
            <span class="cell cell--date mono" title={stock.last_debated_at}>
              {formatDate(stock.last_debated_at)}
            </span>
          </button>
        {/each}
      </div>
    </div>
  </div>

  {#if sorted.length === 0}
    <div class="empty-state">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <p>Tidak ada hasil ditemukan</p>
      {#if $searchQuery}<span class="text-muted">untuk "{$searchQuery}"</span>{/if}
    </div>
  {/if}
</div>

<style>
  .table-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-4);
    padding: var(--sp-4);
    border-bottom: 1px solid var(--surface-border);
    background: transparent;
  }

  .header-titles h2 {
    margin: 0;
  }

  .panel-actions {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
  }

  .search-wrap {
    position: relative;
    display: flex;
    align-items: center;
    width: 240px;
  }

  .search-icon {
    position: absolute;
    left: 12px;
    color: var(--text-muted);
  }

  .search-input {
    width: 100%;
    height: 38px;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    padding: 0 36px;
    background: var(--surface-1);
    color: var(--text-primary);
    outline: none;
    font-size: 14px;
    transition: all 0.2s;
  }

  .search-input:focus {
    border-color: var(--accent-cyan);
    box-shadow: 0 0 0 2px var(--accent-cyan-dim);
  }

  .clear-btn {
    position: absolute;
    right: 8px;
    background: transparent;
    border: none;
    color: var(--text-secondary);
    padding: 4px;
    border-radius: 4px;
  }

  .clear-btn:hover {
    background: var(--surface-2);
    color: var(--text-primary);
  }

  .debate-btn {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .export-btn {
    font-weight: 500;
  }

  .table-head,
  .table-row {
    display: grid;
    grid-template-columns: 40px 90px minmax(180px, 1fr) 140px 110px 110px 100px 160px 120px;
    align-items: center;
    width: 100%;
    min-width: 950px;
  }

  .table-head {
    min-height: 48px;
    border-bottom: 1px solid var(--surface-border);
    background: var(--surface-1);
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .table-body {
    flex: 1;
    overflow: auto;
    outline: none;
  }

  .virtual-space {
    position: relative;
    min-width: 800px;
  }

  .virtual-window {
    position: absolute;
    inset: 0 0 auto 0;
  }

  .table-row {
    border: 0;
    border-bottom: 1px solid var(--surface-border);
    padding: 0;
    background: transparent;
    color: var(--text-primary);
    text-align: left;
    transition: background 0.15s;
  }

  .table-row:hover {
    background: var(--surface-2);
  }

  .table-row--active {
    background: var(--accent-cyan-dim);
    box-shadow: inset 4px 0 0 var(--accent-cyan);
  }

  .table-row--active:hover {
    background: var(--accent-cyan-dim);
  }

  .cell {
    min-width: 0;
    padding: 0 var(--sp-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: flex;
    align-items: center;
  }

  .cell--check {
    justify-content: center;
  }

  .cell--negative {
    color: var(--signal-bear);
  }

  .sortable {
    height: 100%;
    border: 0;
    background: transparent;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }

  .sortable:hover {
    color: var(--text-primary);
  }

  .ticker-code {
    position: relative;
    font-size: 15px;
  }

  .ticker-code strong {
    font-family: var(--font-sans);
    font-weight: 700;
  }

  .quick-debate {
    position: absolute;
    right: 8px;
    top: 50%;
    transform: translateY(-50%);
    width: 24px;
    height: 24px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    background: var(--signal-bull);
    color: #fff;
    font-size: 10px;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s, transform 0.2s;
  }

  .table-row:hover .quick-debate {
    opacity: 1;
    pointer-events: auto;
  }

  .quick-debate:hover {
    transform: translateY(-50%) scale(1.1);
  }

  .quick-debate--disabled {
    opacity: 0 !important;
    pointer-events: none;
  }

  .company-cell {
    color: var(--text-secondary);
    font-size: 14px;
  }

  .conviction-cell {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
    width: 100%;
    padding-right: var(--sp-4);
  }

  .conv-score {
    font-size: 13px;
    font-weight: 700;
    font-family: var(--font-mono);
  }

  .text-progress-bar {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.5px;
    user-select: none;
  }

  .muted-blocks {
    color: var(--text-muted);
  }

  .badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 10px;
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--badge-color) 15%, transparent);
    color: var(--badge-color);
    font-size: 12px;
    font-weight: 700;
    border: 1px solid color-mix(in srgb, var(--badge-color) 30%, transparent);
  }

  .cell--price,
  .cell--edge,
  .cell--entry,
  .cell--date {
    font-size: 14px;
  }

  .cell--edge {
    color: var(--signal-bull);
    font-weight: 700;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: var(--sp-10);
    color: var(--text-secondary);
    height: 100%;
    gap: var(--sp-2);
  }

  .empty-state svg {
    opacity: 0.5;
    margin-bottom: var(--sp-2);
  }

  .empty-state p {
    margin: 0;
    font-size: 16px;
    font-weight: 500;
    color: var(--text-primary);
  }

  @media (max-width: 1024px) {
    .panel-header {
      flex-direction: column;
      align-items: flex-start;
    }
    .panel-actions {
      width: 100%;
      flex-wrap: wrap;
    }
    .search-wrap {
      flex: 1;
      min-width: 200px;
    }
  }
</style>
