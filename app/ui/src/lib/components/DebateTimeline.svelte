<script lang="ts">
  import { afterUpdate } from 'svelte';
  import { activeTicker, allResults, debateStream, isStreaming } from '$lib/stores/dashboard';
  import type { DebateEvent } from '$lib/types';

  let timelineEl: HTMLDivElement;

  afterUpdate(() => {
    if (timelineEl && $isStreaming) {
      timelineEl.scrollTop = timelineEl.scrollHeight;
    }
  });

  function formatMetricKey(key: string): string {
    return key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  $: activeResult = $allResults.find((item) => item.ticker === $activeTicker);
  $: events = $debateStream.filter((event: DebateEvent) => !$activeTicker || event.ticker === $activeTicker);
</script>

<div class="debate-panel">
  {#if !$activeTicker}
    <div class="placeholder">
      <div class="placeholder__text">Pilih saham dari tabel untuk melihat analisis debate</div>
    </div>
  {:else}
    <div class="debate-header">
      <h2 class="debate-ticker">{$activeTicker}</h2>
      {#if $isStreaming}
        <div class="streaming-indicator">
          <span class="streaming-dot"></span>
          <span>Streaming debate</span>
        </div>
      {/if}
    </div>

    {#if activeResult?.devil_advocate_triggered}
      <div class="devil-banner">
        <div class="devil-banner__title">Devil's Advocate Triggered</div>
        <div class="devil-banner__sub">Konsensus terlalu cepat, babak tambahan dipakai untuk menguji keyakinan.</div>
      </div>
    {/if}

    <div class="timeline" bind:this={timelineEl}>
      {#each events as event}
        {#if event.type === 'start'}
          <div class="timeline-item timeline-item--system">
            <div class="tl-dot"></div>
            <div class="system-line">Mulai debate {event.ticker}</div>
          </div>
        {:else if event.type === 'scout'}
          <div class="timeline-item timeline-item--scout">
            <div class="tl-dot tl-dot--scout"></div>
            <div class="scout-card">
              <div class="scout-card__title">Scouting Report</div>
              <div class="scout-grid">
                {#each Object.entries(event.metrics) as [category, metrics]}
                  <div class="scout-col">
                    <div class="scout-col__header">{category.toUpperCase()}</div>
                    {#each Object.entries(metrics) as [key, value]}
                      <div class="scout-row">
                        <span class="scout-key">{formatMetricKey(key)}</span>
                        <span class="scout-val">{value}</span>
                      </div>
                    {/each}
                  </div>
                {/each}
              </div>
            </div>
          </div>
        {:else if event.type === 'round'}
          <div class="timeline-item timeline-item--round">
            <div class="round-label">Round {event.data.round}</div>
            <div class="bubble bubble--bull">
              <div class="bubble__header">
                <span class="bubble__role">Bull</span>
                {#if event.data.score_delta > 0}
                  <span class="delta delta--pos">+{event.data.score_delta}</span>
                {/if}
              </div>
              <p class="bubble__text">{event.data.bull_argument}</p>
            </div>
            <div class="bubble bubble--bear">
              <div class="bubble__header">
                <span class="bubble__role">Bear</span>
                {#if event.data.score_delta < 0}
                  <span class="delta delta--neg">{event.data.score_delta}</span>
                {/if}
              </div>
              <p class="bubble__text">{event.data.bear_argument}</p>
            </div>
          </div>
        {:else if event.type === 'verdict'}
          <div class="timeline-item timeline-item--verdict">
            <div class="verdict-card">
              <div class="verdict-card__header">Verdict</div>
              <p class="verdict-card__text">{event.result.verdict_summary}</p>
              <div class="verdict-score">
                Conviction: <strong>{event.result.conviction_score}</strong> / 100
              </div>
            </div>
          </div>
        {:else if event.type === 'error'}
          <div class="timeline-item timeline-item--error">
            <div class="error-line">{event.message}</div>
          </div>
        {/if}
      {/each}

      {#if activeResult && events.length === 0}
        <div class="verdict-card">
          <div class="verdict-card__header">Latest Verdict</div>
          <p class="verdict-card__text">{activeResult.verdict_summary}</p>
        </div>
      {/if}

      {#if $isStreaming}
        <div class="typing-indicator"><span></span><span></span><span></span></div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .debate-panel {
    min-height: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .placeholder {
    min-height: 180px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    text-align: center;
  }

  .debate-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-4);
    margin-bottom: var(--sp-3);
  }

  .debate-ticker {
    margin: 0;
    color: var(--text-code);
    font-family: var(--font-mono);
    font-size: 20px;
  }

  .streaming-indicator {
    display: inline-flex;
    align-items: center;
    gap: var(--sp-2);
    color: var(--accent-cyan);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .streaming-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-cyan);
    box-shadow: 0 0 8px var(--accent-cyan);
  }

  .devil-banner {
    margin-bottom: var(--sp-3);
    border: 1px solid var(--accent-gold);
    border-radius: var(--radius-md);
    padding: var(--sp-3);
    background: var(--accent-gold-dim);
  }

  .devil-banner__title {
    color: var(--accent-gold);
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
  }

  .devil-banner__sub {
    margin-top: var(--sp-1);
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.4;
  }

  .timeline {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding-right: var(--sp-2);
  }

  .timeline-item {
    position: relative;
    margin-bottom: var(--sp-4);
  }

  .tl-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--surface-border);
  }

  .tl-dot--scout {
    background: var(--accent-cyan);
  }

  .system-line,
  .error-line {
    margin-top: var(--sp-2);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .error-line {
    color: var(--signal-bear);
  }

  .scout-card,
  .verdict-card,
  .bubble {
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    background: var(--surface-1);
  }

  .scout-card {
    padding: var(--sp-3);
  }

  .scout-card__title,
  .verdict-card__header,
  .round-label {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }

  .scout-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--sp-2);
    margin-top: var(--sp-3);
  }

  .scout-col {
    min-width: 0;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: var(--sp-2);
    background: var(--surface-base);
  }

  .scout-col__header {
    margin-bottom: var(--sp-2);
    color: var(--accent-cyan);
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .scout-row {
    display: grid;
    gap: 2px;
    margin-bottom: var(--sp-2);
  }

  .scout-key {
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .scout-val {
    overflow-wrap: anywhere;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .round-label {
    margin-bottom: var(--sp-2);
  }

  .bubble {
    padding: var(--sp-3);
    margin-bottom: var(--sp-2);
  }

  .bubble--bull {
    border-color: rgba(34, 197, 94, 0.35);
  }

  .bubble--bear {
    border-color: rgba(239, 68, 68, 0.35);
  }

  .bubble__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-2);
    margin-bottom: var(--sp-2);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .bubble__role {
    color: var(--text-code);
  }

  .delta--pos {
    color: var(--signal-bull);
  }

  .delta--neg {
    color: var(--signal-bear);
  }

  .bubble__text,
  .verdict-card__text {
    margin: 0;
    color: var(--text-secondary);
    font-size: 12px;
    line-height: 1.55;
  }

  .verdict-card {
    padding: var(--sp-3);
  }

  .verdict-card__text {
    margin-top: var(--sp-2);
  }

  .verdict-score {
    margin-top: var(--sp-3);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .verdict-score strong {
    color: var(--accent-cyan);
  }

  .typing-indicator {
    display: flex;
    gap: var(--sp-1);
    padding: var(--sp-3);
  }

  .typing-indicator span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent-cyan);
    animation: blink 1s infinite ease-in-out;
  }

  .typing-indicator span:nth-child(2) {
    animation-delay: 0.15s;
  }

  .typing-indicator span:nth-child(3) {
    animation-delay: 0.3s;
  }

  @keyframes blink {
    0%,
    80%,
    100% {
      opacity: 0.25;
    }

    40% {
      opacity: 1;
    }
  }
</style>
