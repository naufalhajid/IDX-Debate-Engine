<script lang="ts">
  import { afterUpdate } from 'svelte';
  import { activeTicker, allResults, debateStream, isStreaming } from '$lib/stores/dashboard';
  import { stockMap, resolveCompanyName, formatSector } from '$lib/stores/metadata';
  import { formatMarkdown } from '$lib/formatMarkdown';
  import type { DebateEvent, DebateRound, Rating, ScoutMetrics } from '$lib/types';

  let timelineEl: HTMLDivElement;
  let userScrolledUp = false;

  const scoutCategories: { key: keyof ScoutMetrics; label: string }[] = [
    { key: 'technical', label: 'Teknikal' },
    { key: 'fundamental', label: 'Fundamental' },
    { key: 'sentiment', label: 'Sentimen' }
  ];

  function onScroll() {
    const el = timelineEl;
    if (!el) return;
    const threshold = 80;
    userScrolledUp = el.scrollHeight - el.scrollTop - el.clientHeight > threshold;
  }

  afterUpdate(() => {
    if (!userScrolledUp && timelineEl) {
      timelineEl.scrollTop = timelineEl.scrollHeight;
    }
  });

  function formatKey(raw: string): string {
    return raw
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function pct(value: number) {
    return Math.max(0, Math.min(100, value));
  }

  function ratingColor(rating: Rating) {
    if (rating.includes('BUY')) return 'var(--signal-bull)';
    if (rating === 'AVOID') return 'var(--signal-bear)';
    return 'var(--signal-hold)';
  }

  $: activeResult = $allResults.find((item) => item.ticker === $activeTicker);
  $: events = $debateStream.filter((event: DebateEvent) => !$activeTicker || event.ticker === $activeTicker);
  $: fallbackRounds = activeResult?.debate_rounds ?? [];
  $: hasDevilEvent = events.some((event) => event.type === 'devil_advocate');
</script>

<div class="debate-panel terminal-panel">
  {#if !$activeTicker}
    <div class="placeholder">
      <div class="placeholder__text">Pilih saham dari tabel untuk melihat ruang debat</div>
    </div>
  {:else}
    <header class="debate-header">
      <div class="header-row">
        <span class="panel-heading">Ruang Debat</span>
        <span class="header-menu">...</span>
      </div>
      <h2>{$activeTicker}</h2>
      <p>{resolveCompanyName($stockMap, $activeTicker ?? '', activeResult?.sector)} · {formatSector(activeResult?.sector)}</p>
      <div class="engine-row">
        <span>MESIN DEBAT IDX v4.1</span>
        <span class="engine-pill">{Math.max(1, fallbackRounds.length || 3)}/3</span>
      </div>
    </header>

    {#if activeResult?.devil_advocate_triggered && !hasDevilEvent}
      <div class="devil-banner">
        <span class="devil-banner__mark">!</span>
        <div>
          <strong>Devil's Advocate Aktif</strong>
          <span>Konsensus diuji ulang sebelum verdict final.</span>
        </div>
      </div>
    {/if}

    <div class="timeline-title">Linimasa Debat</div>

    <div class="timeline" bind:this={timelineEl} onscroll={onScroll}>
      {#each events as event, index (`${event.type}-${event.ticker}-${index}`)}
        {#if event.type === 'progress'}
          <div class="progress-line">
            <span>{formatKey(event.phase)}</span>
            <strong>{pct(event.pct)}%</strong>
            <span class="progress-track">
              <span style="width: {pct(event.pct)}%"></span>
            </span>
          </div>

        {:else if event.type === 'scout'}
          <div class="scout-card">
            <div class="scout-card__title">Laporan Scout</div>
            <div class="scout-grid">
              {#each scoutCategories as category (category.key)}
                <section class="scout-col">
                  <div class="scout-col__header">{category.label}</div>
                  {#each Object.entries(event.metrics[category.key]) as [key, value] (key)}
                    <div class="scout-row">
                      <span class="scout-key">{formatKey(key)}</span>
                      <span class="scout-val">{value}</span>
                    </div>
                  {/each}
                </section>
              {/each}
            </div>
          </div>

        {:else if event.type === 'round'}
          {@render roundBubble(event.data)}

        {:else if event.type === 'devil_advocate'}
          <div class="devil-banner devil-banner--event">
            <span class="devil-banner__mark">!</span>
            <div>
              <strong>⚠ Devil's Advocate Triggered</strong>
              <span>Konsensus tercapai terlalu cepat — babak tambahan dipaksa untuk menguji ketahanan argumen.</span>
            </div>
          </div>

        {:else if event.type === 'verdict'}
          <div class="verdict-card verdict-card--final">
            <div class="verdict-card__top">
              <div>
                <span>Verdict Final</span>
                <strong>{event.result.ticker}</strong>
              </div>
              <span class="rating-chip" style="--rating-color: {ratingColor(event.result.rating)}">
                {event.result.rating.replace('_', ' ')}
              </span>
            </div>
            <div class="verdict-score-row">
              <strong>{event.result.conviction_score}</strong>
              <span>/100 keyakinan</span>
            </div>
            <div class="verdict-bar">
              <span
                style="width: {pct(event.result.conviction_score)}%; background: {ratingColor(event.result.rating)}"
              ></span>
            </div>
            <div class="speech-content">{@html formatMarkdown(event.result.verdict_summary)}</div>
          </div>

        {:else if event.type === 'done'}
          <div class="done-line">✓ Analisis selesai</div>

        {:else if event.type === 'error'}
          <div class="error-banner">
            <strong>✕ Terjadi Kesalahan</strong>
            <span>{event.message}</span>
          </div>
        {/if}
      {/each}

      {#if activeResult && events.length === 0}
        {#each fallbackRounds as round (round.round)}
          {@render roundBubble(round)}
        {/each}
        <div class="verdict-card verdict-card--final">
          <div class="verdict-card__top">
            <div>
              <span>Verdict Terakhir</span>
              <strong>{activeResult.ticker}</strong>
            </div>
            <span class="rating-chip" style="--rating-color: {ratingColor(activeResult.rating)}">
              {activeResult.rating.replace('_', ' ')}
            </span>
          </div>
          <div class="verdict-score-row">
            <strong>{activeResult.conviction_score}</strong>
            <span>/100 keyakinan</span>
          </div>
          <div class="verdict-bar">
            <span
              style="width: {pct(activeResult.conviction_score)}%; background: {ratingColor(activeResult.rating)}"
            ></span>
          </div>
          <div class="speech-content">{@html formatMarkdown(activeResult.verdict_summary)}</div>
        </div>
      {/if}

      {#if $isStreaming}
        <div class="typing-indicator"><span></span><span></span><span></span></div>
      {/if}
    </div>
  {/if}
</div>

{#snippet roundBubble(round: DebateRound)}
  <div class="round-block">
    <div class="time-label">Ronde {round.round}</div>

    <div class="speech-row speech-row--bull">
      <div class="role-badge">
        <strong>🐂 Bull</strong>
        {#if round.score_delta > 0}
          <span class="delta delta--pos">+{round.score_delta}</span>
        {/if}
      </div>
      <div class="speech-bubble">
        <div class="speech-content">{@html formatMarkdown(round.bull_argument)}</div>
      </div>
    </div>

    <div class="speech-row speech-row--bear">
      <div class="role-badge">
        <strong>🐻 Bear</strong>
        {#if round.score_delta < 0}
          <span class="delta delta--neg">{round.score_delta}</span>
        {/if}
      </div>
      <div class="speech-bubble">
        <div class="speech-content">{@html formatMarkdown(round.bear_argument)}</div>
      </div>
    </div>
  </div>
{/snippet}

<style>
  .debate-panel {
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .placeholder {
    min-height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--sp-4);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    text-align: center;
  }

  .debate-header {
    border-bottom: 1px solid var(--surface-border);
    padding: var(--sp-3);
  }

  .header-row,
  .engine-row,
  .verdict-card__top,
  .verdict-score-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-2);
  }

  .header-menu {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 13px;
  }

  .debate-header h2 {
    margin: var(--sp-2) 0 0;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 22px;
    line-height: 1;
  }

  .debate-header p {
    margin: 3px 0 var(--sp-3);
    color: var(--text-secondary);
    font-size: 12px;
  }

  .engine-row {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 800;
  }

  .engine-pill,
  .rating-chip {
    border: 1px solid var(--signal-bull);
    border-radius: 999px;
    padding: 2px 6px;
    color: var(--signal-bull);
    background: var(--signal-bull-dim);
  }

  .timeline-title {
    padding: var(--sp-3) var(--sp-3) var(--sp-1);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 800;
    text-transform: uppercase;
  }

  .timeline {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 0 var(--sp-3) var(--sp-3);
  }

  .scout-card,
  .progress-line,
  .verdict-card,
  .devil-banner,
  .error-banner {
    margin-bottom: var(--sp-3);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    padding: var(--sp-2);
    background: var(--surface-1);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .progress-line {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: var(--sp-2);
    align-items: center;
  }

  .progress-line strong {
    color: var(--accent-cyan);
  }

  .progress-track,
  .verdict-bar {
    grid-column: 1 / -1;
    height: 3px;
    border-radius: 999px;
    background: var(--surface-3);
    overflow: hidden;
  }

  .progress-track span,
  .verdict-bar span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--accent-cyan);
  }

  .scout-card__title {
    color: var(--accent-cyan);
    font-weight: 800;
    text-transform: uppercase;
  }

  .scout-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--sp-2);
    margin-top: var(--sp-2);
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
    color: var(--text-secondary);
    font-size: 9px;
    font-weight: 800;
    text-transform: uppercase;
  }

  .scout-row {
    display: grid;
    gap: 2px;
    margin-bottom: var(--sp-2);
  }

  .scout-row:last-child {
    margin-bottom: 0;
  }

  .scout-key {
    color: var(--text-muted);
    font-size: 9px;
  }

  .scout-val {
    overflow-wrap: anywhere;
    color: var(--text-primary);
    font-size: 10px;
  }

  .round-block {
    display: grid;
    gap: var(--sp-2);
    margin-bottom: var(--sp-3);
  }

  .time-label {
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 9px;
    text-align: center;
    text-transform: uppercase;
  }

  .speech-row {
    display: grid;
    gap: var(--sp-1);
  }

  .role-badge {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-2);
    color: var(--signal-bull);
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 800;
  }

  .speech-row--bear .role-badge {
    color: var(--signal-bear);
  }

  .speech-bubble {
    position: relative;
    border: 1px solid var(--signal-bull);
    border-radius: var(--radius-md);
    padding: var(--sp-2);
    background: var(--signal-bull-dim);
  }

  .speech-row--bear .speech-bubble {
    border-color: var(--signal-bear);
    background: var(--signal-bear-dim);
  }

  .speech-content,
  .error-banner span {
    margin: 0;
    color: var(--text-primary);
    font-size: 10px;
    line-height: 1.45;
    overflow-wrap: anywhere;
  }

  .speech-content :global(strong) {
    color: var(--text-primary);
    font-weight: 800;
  }

  .speech-content :global(em) {
    color: var(--text-code);
    font-style: italic;
  }

  .speech-content :global(ul) {
    margin: 4px 0;
    padding-left: 14px;
    list-style: disc;
  }

  .speech-content :global(li) {
    margin-bottom: 2px;
  }

  .delta {
    font-size: 10px;
    font-weight: 800;
  }

  .delta--pos {
    color: var(--signal-bull);
  }

  .delta--neg {
    color: var(--signal-bear);
  }

  .devil-banner {
    display: grid;
    grid-template-columns: 24px 1fr;
    gap: var(--sp-2);
    border-color: var(--signal-hold);
    background: var(--signal-hold-dim);
  }

  .devil-banner__mark {
    width: 20px;
    height: 20px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    background: var(--signal-hold-dim);
    color: var(--signal-hold);
    font-family: var(--font-mono);
    font-weight: 800;
  }

  .devil-banner strong,
  .devil-banner span,
  .error-banner strong {
    display: block;
  }

  .devil-banner strong {
    color: var(--signal-hold);
  }

  .devil-banner span {
    margin-top: 2px;
    color: var(--text-primary);
    line-height: 1.4;
  }

  .verdict-card--final {
    border-color: var(--accent-cyan);
    background: color-mix(in srgb, var(--accent-cyan) 10%, var(--surface-1));
  }

  .verdict-card__top span,
  .verdict-card__top strong,
  .verdict-score-row strong,
  .verdict-score-row span {
    display: block;
    font-family: var(--font-mono);
  }

  .verdict-card__top span {
    color: var(--text-secondary);
    font-size: 9px;
    text-transform: uppercase;
  }

  .verdict-card__top strong {
    color: var(--text-primary);
    font-size: 14px;
  }

  .rating-chip {
    --rating-color: var(--signal-hold);
    border-color: var(--rating-color);
    color: var(--rating-color);
    background: color-mix(in srgb, var(--rating-color) 16%, transparent);
    font-size: 9px;
    font-weight: 800;
  }

  .verdict-score-row {
    justify-content: flex-start;
    margin: var(--sp-2) 0;
  }

  .verdict-score-row strong {
    color: var(--text-primary);
    font-size: 24px;
    line-height: 1;
  }

  .verdict-score-row span {
    color: var(--text-secondary);
    font-size: 10px;
  }

  .verdict-card .speech-content {
    margin-top: var(--sp-2);
  }

  .done-line {
    margin-bottom: var(--sp-3);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
    text-align: center;
  }

  .error-banner {
    display: grid;
    gap: var(--sp-1);
    border-color: var(--signal-bear);
    background: var(--signal-bear-dim);
  }

  .error-banner strong {
    color: var(--signal-bear);
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .typing-indicator {
    display: flex;
    gap: var(--sp-1);
    padding: var(--sp-2);
  }

  .typing-indicator span {
    width: 5px;
    height: 5px;
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

  @media (max-width: 760px) {
    .scout-grid {
      grid-template-columns: 1fr;
    }
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
