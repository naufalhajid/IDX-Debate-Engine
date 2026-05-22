<script lang="ts">
  import { summaryStats } from '$lib/stores/dashboard';

  export let online = false;
  export let loading = false;
  export let lastUpdated: Date | null = null;

  $: marketCards = [
    { label: 'COMPOSITE', value: '7,155.20', change: '+0.31%' },
    { label: 'STRONG BUY', value: String($summaryStats.strongBuy), change: 'live' },
    { label: 'AVG CONV.', value: String($summaryStats.avgConviction), change: 'score' },
    { label: 'USD/IDR', value: '18,050', change: lastUpdated ? lastUpdated.toLocaleTimeString('id-ID') : 'sync' }
  ];
</script>

<div class="top-strip">
  <div class="quick-tools" aria-label="Quick tools">
    <button class="tool-btn" type="button" title="Menu">
      <span></span><span></span><span></span>
    </button>
    <button class="tool-btn tool-btn--line" type="button" title="Market pulse"></button>
    <button class="tool-btn tool-btn--refresh" type="button" title={loading ? 'Loading' : 'Refresh'}></button>
  </div>

  <div class="market-row">
    {#each marketCards as item}
      <div class="market-card">
        <span class="market-card__label">{item.label}</span>
        <strong>{item.value}</strong>
        <span class="market-card__change">{item.change}</span>
      </div>
    {/each}
  </div>

  <div class="status-cluster">
    <span class="status-pill" class:status-pill--online={online}>
      <span></span>
      {online ? 'API' : 'OFF'}
    </span>
    <span class="mini-icon mini-icon--chat" title="Messages"></span>
    <span class="mini-icon mini-icon--bell" title="Alerts"></span>
    <span class="user-dot" title="User"></span>
  </div>
</div>

<style>
  .top-strip {
    min-height: 50px;
    display: grid;
    grid-template-columns: 94px minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--sp-2);
    padding: var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--surface-border);
    background: rgba(5, 10, 15, 0.78);
  }

  .quick-tools {
    display: grid;
    grid-template-columns: repeat(3, 28px);
    gap: var(--sp-1);
  }

  .tool-btn,
  .mini-icon,
  .user-dot {
    position: relative;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    background: rgba(23, 34, 49, 0.72);
  }

  .tool-btn {
    width: 28px;
    height: 28px;
    padding: 0;
  }

  .tool-btn span {
    width: 12px;
    height: 1px;
    display: block;
    margin: 4px auto;
    background: var(--text-secondary);
  }

  .tool-btn--line::before {
    content: '';
    position: absolute;
    inset: 9px 7px 8px;
    border-left: 2px solid transparent;
    border-bottom: 2px solid var(--text-secondary);
    transform: skew(-30deg);
  }

  .tool-btn--refresh::before {
    content: '';
    position: absolute;
    inset: 7px;
    border: 2px solid var(--text-secondary);
    border-left-color: transparent;
    border-radius: 50%;
  }

  .market-row {
    min-width: 0;
    display: grid;
    grid-template-columns: repeat(4, minmax(110px, 1fr));
    gap: var(--sp-2);
  }

  .market-card {
    min-width: 0;
    height: 34px;
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-rows: 13px 17px;
    align-items: center;
    column-gap: var(--sp-2);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: 4px var(--sp-2);
    background: rgba(23, 34, 49, 0.78);
    font-family: var(--font-mono);
  }

  .market-card__label {
    grid-column: 1 / -1;
    color: var(--text-muted);
    font-size: 9px;
    font-weight: 700;
  }

  .market-card strong {
    overflow: hidden;
    color: var(--text-primary);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .market-card__change {
    color: var(--signal-bull);
    font-size: 10px;
    font-weight: 700;
  }

  .status-cluster {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
  }

  .status-pill {
    height: 24px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 1px solid var(--surface-border);
    border-radius: 999px;
    padding: 0 var(--sp-2);
    background: rgba(23, 34, 49, 0.76);
    color: var(--signal-bear);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
  }

  .status-pill span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 8px currentColor;
  }

  .status-pill--online {
    color: var(--signal-bull);
  }

  .mini-icon,
  .user-dot {
    width: 26px;
    height: 26px;
    display: inline-block;
  }

  .mini-icon--chat::before {
    content: '';
    position: absolute;
    inset: 7px 6px 8px;
    border: 2px solid var(--text-secondary);
    border-radius: 3px;
  }

  .mini-icon--bell::before {
    content: '';
    position: absolute;
    left: 8px;
    top: 6px;
    width: 9px;
    height: 11px;
    border: 2px solid var(--text-secondary);
    border-radius: 8px 8px 4px 4px;
  }

  .mini-icon--bell::after {
    content: '';
    position: absolute;
    right: 3px;
    top: 2px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--signal-bear);
    box-shadow: 0 0 8px var(--signal-bear);
  }

  .user-dot::before {
    content: '';
    position: absolute;
    inset: 5px;
    border-radius: 50%;
    background: linear-gradient(180deg, #8fa0b4, #4d5b69);
  }

  @media (max-width: 900px) {
    .top-strip {
      grid-template-columns: 1fr;
    }

    .market-row {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .status-cluster {
      justify-content: flex-end;
    }
  }
</style>
