<script lang="ts">
  import { onMount } from 'svelte';
  import { activeTicker, filteredResults, isStreaming, searchQuery } from '$lib/stores/dashboard';
  import { stockMap, resolveCompanyName } from '$lib/stores/metadata';
  import type { Rating, StockResult } from '$lib/types';

  export let onDebate: (tickers: string[]) => void = () => {};

  const rowHeight = 43;
  const buffer = 7;
  const RATING_CONFIG: Record<Rating, { label: string; color: string }> = {
    STRONG_BUY: { label: 'STRONG BUY', color: 'var(--signal-bull)' },
    BUY: { label: 'BUY', color: '#42df94' },
    HOLD: { label: 'HOLD', color: 'var(--signal-hold)' },
    AVOID: { label: 'AVOID', color: 'var(--signal-bear)' }
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

  function formatPrice(value: number) {
    return value.toLocaleString('id-ID');
  }

  function formatEntry(stock: StockResult) {
    return `${formatPrice(stock.entry_low)} - ${formatPrice(stock.entry_high)}`;
  }

  function debateSlot(index: number) {
    const hour = 14 + Math.floor(index / 2);
    const minute = index % 2 === 0 ? '00' : '30';
    return `${String(hour).padStart(2, '0')}:${minute} GMT`;
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

<div class="table-panel terminal-panel">
  <header class="panel-header">
    <div>
      <div class="panel-heading">Stock Candidates</div>
      <div class="panel-subtitle">Active stock tickers and active stock to tickers</div>
    </div>
    <div class="panel-actions">
      <div class="search-wrap">
        <span class="search-key">K</span>
        <input
          class="search-input"
          placeholder="Ticker / sector"
          bind:value={$searchQuery}
        />
        {#if $searchQuery}
          <button class="clear-btn" onclick={() => searchQuery.set('')} type="button">x</button>
        {/if}
      </div>
      {#if selectedCount > 0}
        <button
          class="debate-btn"
          disabled={$isStreaming}
          onclick={() => onDebate([...selectedTickers])}
          type="button"
        >
          Debate {selectedCount}
        </button>
      {/if}
      <button class="menu-dot" onclick={exportCSV} title="Export CSV (E)" type="button">CSV</button>
    </div>
  </header>

  <div class="table-head">
    <label class="cell cell--check" title="Select all">
      <input type="checkbox" checked={allChecked} onchange={(event) => toggleAll(isChecked(event))} />
    </label>
    <button class="cell cell--ticker sortable" onclick={() => sort('ticker')} type="button">
      Ticker {sortKey === 'ticker' ? (sortDir === 'desc' ? 'v' : '^') : ''}
    </button>
    <div class="cell cell--company">Company</div>
    <button class="cell cell--conv sortable" onclick={() => sort('conviction_score')} type="button">
      Conviction<br />Score {sortKey === 'conviction_score' ? (sortDir === 'desc' ? 'v' : '^') : ''}
    </button>
    <div class="cell cell--rating">Rating</div>
    <button class="cell cell--price sortable" onclick={() => sort('entry_low')} type="button">
      Price<br />(IDR)
    </button>
    <button class="cell cell--edge sortable" onclick={() => sort('risk_reward')} type="button">
      R/R<br />Edge
    </button>
    <div class="cell cell--entry">Entry<br />Range</div>
    <div class="cell cell--next">Next<br />Debate</div>
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
        {#each visibleRows as stock, index (stock.ticker)}
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
            <span class="cell cell--ticker ticker-code">
              {stock.ticker}
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
            <span class="cell cell--company company-cell">{resolveCompanyName($stockMap, stock.ticker, stock.sector)}</span>
            <span class="cell cell--conv conviction-cell">
              <span>{stock.conviction_score}%</span>
              <span class="mini-bar">
                <span style="width: {stock.conviction_score}%; background: {cfg.color}"></span>
              </span>
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
            <span class="cell cell--next mono">{debateSlot(index)}</span>
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
    overflow: hidden;
  }

  .panel-header {
    min-height: 52px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-3);
    border-bottom: 1px solid rgba(118, 139, 164, 0.14);
    padding: var(--sp-2) var(--sp-3);
  }

  .panel-actions {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
  }

  .search-wrap {
    width: 174px;
    position: relative;
    display: flex;
    align-items: center;
  }

  .search-key {
    position: absolute;
    left: var(--sp-2);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 800;
  }

  .search-input {
    width: 100%;
    height: 28px;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: 0 24px 0 24px;
    background: rgba(3, 8, 13, 0.44);
    color: var(--text-primary);
    outline: none;
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .clear-btn {
    position: absolute;
    right: 5px;
    border: 0;
    background: transparent;
    color: var(--text-secondary);
    font-family: var(--font-mono);
  }

  .debate-btn,
  .menu-dot {
    height: 28px;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: 0 var(--sp-2);
    background: rgba(23, 34, 49, 0.72);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 800;
    text-transform: uppercase;
  }

  .debate-btn {
    border-color: rgba(32, 208, 131, 0.46);
    color: var(--signal-bull);
    background: var(--signal-bull-dim);
  }

  .table-head,
  .table-row {
    display: grid;
    grid-template-columns: 28px 58px minmax(120px, 1fr) 78px 88px 78px 72px 112px 82px;
    align-items: center;
  }

  .table-head {
    min-height: 38px;
    border-bottom: 1px solid rgba(118, 139, 164, 0.13);
    background: rgba(5, 10, 15, 0.34);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 800;
    line-height: 1.05;
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
    min-width: 716px;
  }

  .virtual-window {
    position: absolute;
    inset: 0 0 auto 0;
  }

  .table-row {
    width: 100%;
    border: 0;
    border-bottom: 1px solid rgba(118, 139, 164, 0.11);
    padding: 0;
    background: transparent;
    color: var(--text-primary);
    text-align: left;
  }

  .table-row:hover,
  .table-row--active {
    background: linear-gradient(90deg, rgba(32, 208, 131, 0.16), rgba(32, 208, 131, 0.04));
  }

  .table-row--active {
    box-shadow: inset 3px 0 0 var(--signal-bull);
  }

  .cell {
    min-width: 0;
    padding: 0 var(--sp-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cell--check {
    display: inline-flex;
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
  }

  .ticker-code {
    position: relative;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 800;
  }

  .quick-debate {
    position: absolute;
    right: 2px;
    top: 50%;
    transform: translateY(-50%);
    width: 18px;
    height: 18px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid rgba(32, 208, 131, 0.5);
    border-radius: var(--radius-sm);
    padding: 0;
    background: var(--signal-bull-dim);
    color: var(--signal-bull);
    font-size: 8px;
    line-height: 1;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.15s ease;
  }

  .table-row:hover .quick-debate {
    opacity: 1;
    pointer-events: auto;
  }

  .quick-debate:hover {
    background: rgba(32, 208, 131, 0.3);
    box-shadow: 0 0 8px rgba(32, 208, 131, 0.2);
  }

  .quick-debate--disabled {
    opacity: 0 !important;
    pointer-events: none;
  }

  .company-cell {
    color: var(--text-secondary);
    font-size: 11px;
  }

  .conviction-cell {
    display: grid;
    gap: 3px;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 800;
  }

  .mini-bar {
    height: 3px;
    border-radius: 999px;
    background: rgba(118, 139, 164, 0.18);
    overflow: hidden;
  }

  .mini-bar span {
    display: block;
    height: 100%;
    border-radius: inherit;
  }

  .badge {
    display: inline-flex;
    align-items: center;
    height: 18px;
    border: 1px solid color-mix(in srgb, var(--badge-color) 56%, transparent);
    border-radius: var(--radius-sm);
    padding: 0 6px;
    color: var(--badge-color);
    background: color-mix(in srgb, var(--badge-color) 18%, transparent);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 800;
  }

  .cell--price,
  .cell--edge,
  .cell--entry,
  .cell--next {
    color: var(--text-code);
    font-size: 10px;
  }

  .cell--edge {
    color: var(--signal-bull);
    font-weight: 800;
  }

  .empty-state {
    padding: var(--sp-10);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    text-align: center;
  }

  @media (max-width: 720px) {
    .panel-header {
      align-items: stretch;
      flex-direction: column;
    }

    .panel-actions,
    .search-wrap {
      width: 100%;
    }
  }
</style>
