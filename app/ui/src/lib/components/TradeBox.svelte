<script lang="ts">
  import { activeTicker, allResults } from '$lib/stores/dashboard';
  import { portfolioEquity } from '$lib/stores/session';
  import { toast } from '$lib/stores/toast';
  import type { Rating } from '$lib/types';

  let riskPercent = 1.5;

  function ratingColor(rating: Rating) {
    if (rating.includes('BUY')) return 'var(--signal-bull)';
    if (rating === 'AVOID') return 'var(--signal-bear)';
    return 'var(--signal-hold)';
  }

  function formatIdr(value: number) {
    return value.toLocaleString('id-ID');
  }

  async function copy(value: number) {
    try {
      await navigator.clipboard.writeText(String(value));
      toast('info', `Copied: ${formatIdr(value)}`);
    } catch {
      toast('error', 'Clipboard tidak tersedia');
    }
  }

  $: result = $allResults.find((item) => item.ticker === $activeTicker);
  $: riskAmount = ($portfolioEquity * riskPercent) / 100;
  $: stopDist = result ? Math.max(result.entry_low - result.stop_loss, 0) : 0;
  $: sharesQty = result && stopDist > 0 ? Math.floor(riskAmount / stopDist) : 0;
  $: lotCount = Math.floor(sharesQty / 100);
  $: positionSize = lotCount * 100 * (result?.entry_low ?? 0);
</script>

{#if result}
  <div class="trade-grid">
    <section class="mini-panel terminal-panel">
      <header>
        <span class="panel-heading">Trade Box</span>
        <span class="rating" style="color: {ratingColor(result.rating)}">{result.rating.replace('_', ' ')}</span>
      </header>

      <div class="trade-ticker">{result.ticker}</div>
      <div class="level-row">
        <span>Entry</span>
        <strong>{formatIdr(result.entry_low)}</strong>
      </div>
      <div class="level-row">
        <span>Target</span>
        <button class="copy-value signal-bull" onclick={() => copy(result!.target_price)} type="button">
          {formatIdr(result.target_price)}
        </button>
      </div>
      <div class="level-row">
        <span>Stop-Loss</span>
        <button class="copy-value signal-bear" onclick={() => copy(result!.stop_loss)} type="button">
          {formatIdr(result.stop_loss)}
        </button>
      </div>
      <div class="rr-strip">
        <span style="width: {Math.min(result.risk_reward / 5, 1) * 100}%"></span>
      </div>
    </section>

    <section class="mini-panel terminal-panel">
      <header>
        <span class="panel-heading">Position Sizer</span>
      </header>

      <div class="sizer-row">
        <label for="trade-equity">Capital</label>
        <input id="trade-equity" class="input input--sm" type="number" step="1000000" bind:value={$portfolioEquity} />
      </div>
      <div class="capital-preview">Rp {formatIdr($portfolioEquity)}</div>

      <div class="sizer-row">
        <label for="risk-percent">Risk %</label>
        <input id="risk-percent" class="input input--sm" type="number" min="0.5" max="10" step="0.5" bind:value={riskPercent} />
      </div>
      <div class="risk-presets">
        {#each [1, 1.5, 2, 3] as pct}
          <button
            class="preset-btn"
            class:preset-btn--active={riskPercent === pct}
            onclick={() => (riskPercent = pct)}
            type="button"
          >{pct}%</button>
        {/each}
      </div>

      <div class="metric-row">
        <span>Risk Amount</span>
        <strong>{formatIdr(riskAmount)} IDR</strong>
      </div>
      <div class="metric-row">
        <span>Stop Loss</span>
        <strong>{stopDist.toLocaleString('id-ID')} IDR/share</strong>
      </div>
      <div class="metric-row metric-row--final">
        <span>Max Size</span>
        <strong>{lotCount.toLocaleString('id-ID')} lot</strong>
      </div>
      <div class="metric-row">
        <span>Current Value</span>
        <strong>{formatIdr(positionSize)}</strong>
      </div>
    </section>
  </div>
{:else}
  <div class="trade-grid trade-grid--empty terminal-panel">
    <div>Pilih saham untuk melihat trade setup</div>
  </div>
{/if}

<style>
  .trade-grid {
    height: 100%;
    display: grid;
    grid-template-columns: 1fr 1.1fr;
    gap: var(--sp-3);
  }

  .mini-panel {
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    padding: var(--sp-2);
  }

  .mini-panel header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-2);
    margin-bottom: var(--sp-2);
  }

  .mini-panel .panel-heading {
    font-size: 11px;
  }

  .rating {
    font-family: var(--font-mono);
    font-size: 8px;
    font-weight: 800;
  }

  .trade-ticker {
    margin-bottom: var(--sp-2);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 800;
  }

  .level-row,
  .metric-row,
  .sizer-row {
    min-height: 19px;
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: var(--sp-2);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 9px;
  }

  .level-row strong,
  .metric-row strong {
    overflow: hidden;
    color: var(--text-primary);
    font-size: 9px;
    text-align: right;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .copy-value {
    border: 0;
    padding: 0;
    background: transparent;
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 800;
    text-align: right;
  }

  .rr-strip {
    height: 3px;
    margin-top: var(--sp-2);
    border-radius: 999px;
    background: rgba(118, 139, 164, 0.16);
    overflow: hidden;
  }

  .rr-strip span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--signal-bull);
  }

  .sizer-row {
    grid-template-columns: 54px 1fr;
    margin-bottom: 4px;
  }

  .sizer-row label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .capital-preview {
    margin: -2px 0 4px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 8px;
    text-align: right;
  }

  .risk-presets {
    display: flex;
    gap: 3px;
    margin-bottom: 6px;
  }

  .preset-btn {
    flex: 1;
    height: 20px;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: 0;
    background: rgba(23, 34, 49, 0.62);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 8px;
    font-weight: 700;
    transition: all 0.12s ease;
  }

  .preset-btn:hover {
    border-color: rgba(24, 211, 255, 0.4);
    color: var(--accent-cyan);
  }

  .preset-btn--active {
    border-color: rgba(32, 208, 131, 0.55);
    background: var(--signal-bull-dim);
    color: var(--signal-bull);
  }

  .metric-row--final strong {
    color: var(--signal-bull);
  }

  .trade-grid--empty {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    text-align: center;
  }

  @media (max-width: 520px) {
    .trade-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
