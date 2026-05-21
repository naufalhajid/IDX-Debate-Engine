<script lang="ts">
  import { onMount } from 'svelte';
  import { activeTicker, filteredResults, isStreaming, searchQuery } from '$lib/stores/dashboard';
  import type { Rating, StockResult } from '$lib/types';

  export let onDebate: (tickers: string[]) => void = () => {};

  const rowHeight = 46;
  const buffer = 6;
  const RATING_CONFIG: Record<Rating, { label: string; color: string }> = {
    STRONG_BUY: { label: 'Strong Buy', color: 'var(--signal-bull)' },
    BUY: { label: 'Buy', color: '#86efac' },
    HOLD: { label: 'Hold', color: 'var(--signal-hold)' },
    AVOID: { label: 'Avoid', color: 'var(--signal-bear)' }
  };

  let selectedTickers = new Set<string>();
  let sortKey: keyof StockResult = 'conviction_score';
  let sortDir: 'asc' | 'desc' = 'desc';
  let scrollTop = 0;
  let viewportHeight = 520;
  let bodyEl: HTMLDivElement | undefined;

  function isChecked(event: Event) {
    return (event.currentTarget as HTMLInputElement).checked;
  }

  function compare(a: StockResult, b: StockResult) {
    const va = a[sortKey];
    const vb = b[sortKey];
    if (typeof va === 'number' && typeof vb === 'number') {
      return sortDir === 'desc' ? vb - va : va - vb;
    }
    return sortDir === 'desc'
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

  $: sorted = [...$filteredResults].sort(compare);
  $: selectedCount = selectedTickers.size;
  $: allChecked = sorted.length > 0 && selectedCount === sorted.length;
  $: startIndex = Math.max(0, Math.floor(scrollTop / rowHeight) - buffer);
  $: visibleCount = Math.ceil(viewportHeight / rowHeight) + buffer * 2;
  $: endIndex = Math.min(sorted.length, startIndex + visibleCount);
  $: visibleRows = sorted.slice(startIndex, endIndex);
  $: offsetY = startIndex * rowHeight;
  $: totalHeight = sorted.length * rowHeight;
</script>

<div class="table-panel">
  <div class="toolbar">
    <div class="search-wrap">
      <span class="search-icon">K</span>
      <input
        class="search-input"
        placeholder="Cari ticker atau sektor..."
        bind:value={$searchQuery}
      />
      {#if $searchQuery}
        <button class="clear-btn" onclick={() => searchQuery.set('')} type="button">Clear</button>
      {/if}
    </div>

    <div class="toolbar__actions">
      {#if selectedCount > 0}
        <span class="selected-badge">{selectedCount} dipilih</span>
        <button
          class="btn btn--accent"
          disabled={$isStreaming}
          onclick={() => onDebate([...selectedTickers])}
          type="button"
        >
          Debate {selectedCount}
        </button>
      {/if}
      <button class="btn btn--ghost" onclick={exportCSV} title="Export CSV (E)" type="button">
        CSV
      </button>
    </div>
  </div>

  <div class="table-head">
    <label class="cell cell--check" title="Select all">
      <input type="checkbox" checked={allChecked} onchange={(event) => toggleAll(isChecked(event))} />
    </label>
    <button class="cell cell--ticker sortable" onclick={() => sort('ticker')} type="button">
      Ticker {sortKey === 'ticker' ? (sortDir === 'desc' ? 'v' : '^') : ''}
    </button>
    <div class="cell cell--sector">Sektor</div>
    <button class="cell cell--conv sortable" onclick={() => sort('conviction_score')} type="button">
      Conv. {sortKey === 'conviction_score' ? (sortDir === 'desc' ? 'v' : '^') : ''}
    </button>
    <div class="cell cell--rating">Rating</div>
    <button class="cell cell--rr sortable" onclick={() => sort('risk_reward')} type="button">
      R/R {sortKey === 'risk_reward' ? (sortDir === 'desc' ? 'v' : '^') : ''}
    </button>
    <div class="cell cell--price">Target</div>
    <div class="cell cell--status">Status</div>
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
            <span class="cell cell--ticker ticker-code">{stock.ticker}</span>
            <span class="cell cell--sector sector-cell">{stock.sector || '-'}</span>
            <span class="cell cell--conv conviction-cell">
              <span class="conv-bar">
                <span
                  class="conv-fill"
                  style="width: {stock.conviction_score}%; background: {cfg.color}"
                ></span>
              </span>
              <span class="conv-num mono">{stock.conviction_score}</span>
            </span>
            <span class="cell cell--rating">
              <span class="badge" style="--badge-color: {cfg.color}">{cfg.label}</span>
            </span>
            <span class="cell cell--rr mono">{stock.risk_reward.toFixed(2)}x</span>
            <span class="cell cell--price mono">Rp {stock.target_price.toLocaleString('id-ID')}</span>
            <span class="cell cell--status">
              {#if stock.actionable}
                <span class="pill pill--on">Actionable</span>
              {:else}
                <span class="pill pill--off">Watch</span>
              {/if}
            </span>
          </button>
        {/each}
      </div>
    </div>
  </div>

  {#if sorted.length === 0}
    <div class="empty-state">
      <div>Tidak ada hasil ditemukan</div>
      {#if $searchQuery}<div class="text-muted">untuk "{$searchQuery}"</div>{/if}
    </div>
  {/if}
</div>

<style>
  .table-panel {
    min-height: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
    background: var(--surface-base);
  }

  .toolbar {
    min-height: 58px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-4);
    border-bottom: 1px solid var(--surface-border);
    padding: var(--sp-3) var(--sp-4);
  }

  .search-wrap {
    width: min(460px, 100%);
    position: relative;
    display: flex;
    align-items: center;
  }

  .search-icon {
    position: absolute;
    left: var(--sp-3);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .search-input {
    width: 100%;
    min-height: 34px;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: 0 72px 0 34px;
    background: var(--surface-1);
    color: var(--text-primary);
    outline: none;
  }

  .search-input:focus {
    border-color: var(--accent-cyan);
    box-shadow: 0 0 0 3px var(--accent-cyan-dim);
  }

  .clear-btn {
    position: absolute;
    right: var(--sp-2);
    border: 0;
    background: transparent;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .toolbar__actions {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
  }

  .selected-badge {
    border: 1px solid var(--accent-gold);
    border-radius: var(--radius-sm);
    padding: 6px var(--sp-2);
    color: var(--accent-gold);
    background: var(--accent-gold-dim);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .table-head,
  .table-row {
    display: grid;
    grid-template-columns: 42px 92px minmax(120px, 1fr) 150px 116px 84px 132px 108px;
    align-items: center;
  }

  .table-head {
    min-height: 36px;
    border-bottom: 1px solid var(--surface-border);
    background: var(--surface-1);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    text-transform: uppercase;
  }

  .table-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    outline: none;
  }

  .virtual-space {
    position: relative;
    min-width: 846px;
  }

  .virtual-window {
    position: absolute;
    inset: 0 0 auto 0;
  }

  .table-row {
    width: 100%;
    border: 0;
    border-bottom: 1px solid rgba(42, 52, 65, 0.7);
    padding: 0;
    background: transparent;
    color: var(--text-primary);
    text-align: left;
  }

  .table-row:hover,
  .table-row--active {
    background: var(--accent-cyan-dim);
  }

  .cell {
    min-width: 0;
    padding: 0 var(--sp-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cell--check {
    display: inline-flex;
    justify-content: center;
  }

  .sortable {
    height: 100%;
    border: 0;
    background: transparent;
    color: inherit;
    text-align: left;
  }

  .ticker-code {
    color: var(--text-code);
    font-family: var(--font-mono);
    font-weight: 600;
  }

  .sector-cell {
    color: var(--text-secondary);
  }

  .conviction-cell {
    display: grid;
    grid-template-columns: 1fr 38px;
    align-items: center;
    gap: var(--sp-2);
  }

  .conv-bar {
    height: 6px;
    border-radius: 999px;
    background: var(--surface-3);
    overflow: hidden;
  }

  .conv-fill {
    display: block;
    height: 100%;
    border-radius: inherit;
  }

  .conv-num {
    color: var(--text-primary);
    font-size: 12px;
  }

  .badge {
    border: 1px solid var(--badge-color);
    border-radius: var(--radius-sm);
    padding: 4px 7px;
    color: var(--badge-color);
    background: color-mix(in srgb, var(--badge-color) 14%, transparent);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .pill {
    border-radius: var(--radius-sm);
    padding: 4px 7px;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .pill--on {
    color: var(--signal-bull);
    background: var(--signal-bull-dim);
  }

  .pill--off {
    color: var(--signal-hold);
    background: var(--signal-hold-dim);
  }

  .empty-state {
    padding: var(--sp-10);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    text-align: center;
  }
</style>
