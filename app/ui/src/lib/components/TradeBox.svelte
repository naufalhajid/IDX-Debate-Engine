<script lang="ts">
  import { activeTicker, allResults } from '$lib/stores/dashboard';
  import { portfolioEquity } from '$lib/stores/session';
  import { toast } from '$lib/stores/toast';
  import type { Rating } from '$lib/types';

  let riskPercent = 2;

  function ratingColor(rating: Rating) {
    if (rating.includes('BUY')) return 'var(--signal-bull)';
    if (rating === 'AVOID') return 'var(--signal-bear)';
    return 'var(--signal-hold)';
  }

  function gaugePath(ratio: number) {
    const angle = Math.min(Math.max(ratio, 0) / 5, 1) * Math.PI;
    const x = 60 - 50 * Math.cos(angle);
    const y = 65 - 50 * Math.sin(angle);
    return `M 10 65 A 50 50 0 0 1 ${x.toFixed(2)} ${y.toFixed(2)}`;
  }

  async function copy(value: number) {
    try {
      await navigator.clipboard.writeText(String(value));
      toast('info', `Copied: ${value.toLocaleString('id-ID')}`);
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
  $: rrRatio = result?.risk_reward ?? 0;
</script>

{#if result}
  <div class="tradebox">
    <div class="tradebox__header">
      <span class="tradebox__ticker">{result.ticker}</span>
      <span class="tradebox__rating" style="color: {ratingColor(result.rating)}">
        {result.rating.replace('_', ' ')}
      </span>
    </div>

    <div class="levels">
      <div class="level">
        <span class="level__label">Entry Range</span>
        <span class="level__val mono">
          {result.entry_low.toLocaleString('id-ID')} - {result.entry_high.toLocaleString('id-ID')}
        </span>
      </div>
      <div class="level">
        <span class="level__label">Target</span>
        <button class="level__val level__val--copy mono signal-bull" onclick={() => copy(result!.target_price)} type="button">
          {result.target_price.toLocaleString('id-ID')} Copy
        </button>
      </div>
      <div class="level">
        <span class="level__label">Stop Loss</span>
        <button class="level__val level__val--copy mono signal-bear" onclick={() => copy(result!.stop_loss)} type="button">
          {result.stop_loss.toLocaleString('id-ID')} Copy
        </button>
      </div>
    </div>

    <div class="gauge-wrap">
      <svg viewBox="0 0 120 72" class="gauge-svg" aria-label="Risk reward gauge">
        <path
          d="M 10 65 A 50 50 0 0 1 110 65"
          fill="none"
          stroke="var(--surface-3)"
          stroke-width="8"
          stroke-linecap="round"
        />
        {#if rrRatio > 0}
          <path
            d={gaugePath(rrRatio)}
            fill="none"
            stroke={rrRatio >= 2 ? 'var(--signal-bull)' : rrRatio >= 1 ? 'var(--signal-hold)' : 'var(--signal-bear)'}
            stroke-width="8"
            stroke-linecap="round"
          />
        {/if}
        <text
          x="60"
          y="56"
          text-anchor="middle"
          fill="var(--text-primary)"
          font-size="14"
          font-family="var(--font-mono)"
          font-weight="700"
        >
          {rrRatio.toFixed(2)}x
        </text>
        <text x="60" y="68" text-anchor="middle" fill="var(--text-secondary)" font-size="7">
          Risk/Reward
        </text>
      </svg>
    </div>

    <div class="sizer">
      <div class="sizer__header">Position Sizer</div>
      <div class="sizer__row">
        <label for="risk-percent">Risk %</label>
        <input id="risk-percent" class="input input--sm" type="number" min="0.5" max="10" step="0.5" bind:value={riskPercent} />
      </div>
      <div class="sizer__row">
        <label for="trade-equity">Equity</label>
        <input id="trade-equity" class="input input--sm" type="number" step="1000000" bind:value={$portfolioEquity} />
      </div>
      <div class="sizer__result">
        <div class="sizer__stat">
          <span>Lot</span>
          <strong class="mono">{lotCount.toLocaleString('id-ID')}</strong>
        </div>
        <div class="sizer__stat">
          <span>Position</span>
          <strong class="mono">Rp {positionSize.toLocaleString('id-ID')}</strong>
        </div>
        <div class="sizer__stat">
          <span>Max Risk</span>
          <strong class="mono signal-bear">Rp {riskAmount.toLocaleString('id-ID')}</strong>
        </div>
      </div>
    </div>
  </div>
{:else}
  <div class="tradebox tradebox--empty">
    <div>Pilih saham untuk melihat trade setup</div>
  </div>
{/if}

<style>
  .tradebox {
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
  }

  .tradebox__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-3);
  }

  .tradebox__ticker {
    color: var(--text-code);
    font-family: var(--font-mono);
    font-size: 28px;
    font-weight: 600;
  }

  .tradebox__rating {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
  }

  .levels {
    display: grid;
    gap: var(--sp-2);
  }

  .level {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-3);
  }

  .level__label {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .level__val {
    color: var(--text-primary);
    font-size: 12px;
  }

  .level__val--copy {
    border: 0;
    padding: 0;
    background: transparent;
  }

  .gauge-wrap {
    display: flex;
    justify-content: center;
    border-block: 1px solid var(--surface-border);
    padding-block: var(--sp-2);
  }

  .gauge-svg {
    width: min(220px, 100%);
    height: auto;
  }

  .sizer {
    display: grid;
    gap: var(--sp-3);
  }

  .sizer__header {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }

  .sizer__row {
    display: grid;
    grid-template-columns: 72px 1fr;
    align-items: center;
    gap: var(--sp-2);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .sizer__result {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    overflow: hidden;
  }

  .sizer__stat {
    min-width: 0;
    display: grid;
    gap: var(--sp-1);
    padding: var(--sp-2);
    border-right: 1px solid var(--surface-border);
    background: var(--surface-1);
  }

  .sizer__stat:last-child {
    border-right: 0;
  }

  .sizer__stat span {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .sizer__stat strong {
    overflow-wrap: anywhere;
    color: var(--text-primary);
    font-size: 11px;
  }

  .tradebox--empty {
    min-height: 172px;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    text-align: center;
  }
</style>
