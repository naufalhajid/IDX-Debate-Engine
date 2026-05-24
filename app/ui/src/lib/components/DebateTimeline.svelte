<script lang="ts">
  import { afterUpdate } from 'svelte';
  import { activeTicker, allResults, debateStream, isStreaming } from '$lib/stores/dashboard';
  import { stockMap, resolveCompanyName, formatSector } from '$lib/stores/metadata';
  import { formatMarkdown } from '$lib/formatMarkdown';
  import type { DebateEvent, DebateRound, Rating, ScoutMetrics, StockResult } from '$lib/types';

  let timelineEl: HTMLDivElement;
  let userScrolledUp = false;

  const scoutCategories: { key: keyof ScoutMetrics; label: string }[] = [
    { key: 'technical', label: 'Technical' },
    { key: 'fundamental', label: 'Fundamental' },
    { key: 'sentiment', label: 'Sentiment' }
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

  function formatIdr(value: number | null | undefined) {
    if (value === null || value === undefined || isNaN(value)) return '-';
    return value.toLocaleString('id-ID');
  }

  function formatDecimal(value: number | null | undefined): string {
    if (value === null || value === undefined || isNaN(value)) return '-';
    return value.toFixed(2);
  }

  function ratingColor(rating: Rating) {
    if (rating.includes('BUY')) return 'var(--signal-bull)';
    if (rating === 'AVOID') return 'var(--signal-bear)';
    return 'var(--signal-hold)';
  }

  function splitArgumentByHeadings(text: string) {
    if (!text) return [];
    // Split text by "### "
    const parts = text.split(/(?=### )/g);
    return parts.map(part => {
      const match = part.match(/^###\s*([^\n]+)\n([\s\S]*)$/);
      if (match) {
        return { title: match[1].trim(), content: match[2].trim() };
      }
      return { title: null, content: part.trim() };
    }).filter(p => p.content || p.title);
  }

  $: activeResult = $allResults.find((item) => item.ticker === $activeTicker);
  $: events = $debateStream.filter((event: DebateEvent) => !$activeTicker || event.ticker === $activeTicker);
  $: fallbackRounds = activeResult?.debate_rounds ?? [];
  $: hasDevilEvent = events.some((event) => event.type === 'devil_advocate');
</script>

<div class="debate-panel terminal-panel">
  {#if !$activeTicker}
    <div class="placeholder">
      <div class="placeholder__icon">📈</div>
      <div class="placeholder__text">Select a stock from the Recommendations list<br/>to view analysis & AI Debate Chamber</div>
    </div>
  {:else}
    <header class="debate-header">
      <div class="header-top">
        <div class="ticker-info">
          <h2>{$activeTicker}</h2>
          <p>{resolveCompanyName($stockMap, $activeTicker ?? '', activeResult?.sector)}</p>
        </div>
        {#if activeResult}
          <div class="rating-badge" style="--rating-color: {ratingColor(activeResult.rating)}">
            {activeResult.rating.replace('_', ' ')}
          </div>
        {/if}
      </div>
      
      {#if activeResult}
        <div class="price-highlights">
          <div class="price-box">
            <span class="price-label">Current / Entry Low</span>
            <strong class="price-value mono">Rp {formatIdr(activeResult.entry_low)}</strong>
          </div>
          <div class="price-box">
            <span class="price-label">Target Price</span>
            <strong class="price-value mono signal-bull">Rp {formatIdr(activeResult.target_price)}</strong>
          </div>
          <div class="price-box">
            <span class="price-label">Stop Loss</span>
            <strong class="price-value mono signal-bear">Rp {formatIdr(activeResult.stop_loss)}</strong>
          </div>
        </div>
      {/if}
    </header>

    {#if activeResult?.devil_advocate_triggered && !hasDevilEvent}
      <div class="devil-advocate-status-bar">
        <span class="status-indicator-dot"></span>
        <span class="status-text">DEVIL'S ADVOCATE ACTIVE: Consensus stress-test forced.</span>
      </div>
    {/if}

    <div class="timeline-container" bind:this={timelineEl} onscroll={onScroll}>
      {#each events as event, index (`${event.type}-${event.ticker}-${index}`)}
        {#if event.type === 'progress'}
          {@const filledCount = Math.round(pct(event.pct) / 5)}
          {@const emptyCount = Math.max(0, 20 - filledCount)}
          <div class="terminal-progress">
            <div class="terminal-progress-text">
              <span class="mono-label">> RUNNING_PHASE: {formatKey(event.phase).toUpperCase()}...</span>
              <span class="mono-pct">{pct(event.pct)}%</span>
            </div>
            <div class="terminal-progress-bar-text">
              [{'■'.repeat(filledCount)}{'□'.repeat(emptyCount)}]
            </div>
          </div>

        {:else if event.type === 'scout'}
          <div class="scout-summary-terminal">
            <div class="scout-title-bar">
              <span class="scout-title-tag">[METRIC.SCOUT_ANALYSIS]</span>
            </div>
            <div class="scout-grid">
              {#each scoutCategories as category (category.key)}
                <div class="scout-column">
                  <div class="scout-col-header">{category.label.toUpperCase()}</div>
                  <div class="scout-metrics-list">
                    {#each Object.entries(event.metrics[category.key]).slice(0, 3) as [key, value] (key)}
                      <div class="scout-metric-item">
                        <span class="metric-key">{formatKey(key)}:</span>
                        <span class="metric-val mono">{value}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/each}
            </div>
          </div>

        {:else if event.type === 'round'}
          {@render roundBubble(event.data)}

        {:else if event.type === 'devil_advocate'}
          <div class="devil-advocate-panel">
            <div class="devil-advocate-header">
              <span class="devil-advocate-tag">[SYSTEM_OVERRIDE.DEVILS_ADVOCATE]</span>
            </div>
            <div class="devil-advocate-body">
              <p>Consensus reached too quickly. Forcing AI agents to re-examine bull/bear arguments with a skeptical lens.</p>
              {#if event.question}
                <div class="devil-question-box">
                  <div class="devil-question-label">DIAGNOSTIC QUESTION:</div>
                  <div class="devil-question-content">{@html formatMarkdown(event.question)}</div>
                </div>
              {/if}
            </div>
          </div>

        {:else if event.type === 'verdict'}
          {@render finalVerdict(event.result)}

        {:else if event.type === 'done'}
          <div class="status-message success">✓ Analysis completed</div>

        {:else if event.type === 'error'}
          <div class="status-message error">✕ {event.message}</div>
        {/if}
      {/each}

      {#if activeResult && events.length === 0}
        {#each fallbackRounds as round (round.round)}
          {@render roundBubble(round)}
        {/each}
        {@render finalVerdict(activeResult)}
      {/if}

      {#if $isStreaming}
        <div class="terminal-cursor-indicator">
          <span class="blinking-block">■</span>
          <span class="indicator-text">SYSTEM: AI agents are debating...</span>
        </div>
      {/if}
    </div>
  {/if}
</div>

{#snippet roundBubble(round: DebateRound)}
  <div class="terminal-log-round">
    <div class="log-round-divider">
      <span class="log-round-tag">ROUND {round.round}</span>
      <span class="log-round-line"></span>
    </div>
    
    <!-- Bull Argument -->
    <div class="log-entry log-entry--bull">
      <div class="log-entry-header">
        <span class="log-agent-tag">[BULL]</span>
        {#if round.score_delta > 0}
          <span class="log-delta pos">+{round.score_delta} PTS</span>
        {/if}
      </div>
      <div class="log-entry-body">
        {#each splitArgumentByHeadings(round.bull_argument) as part}
          <div class="log-content-block">
            {#if part.title}
              <div class="log-block-title">{part.title}</div>
            {/if}
            <div class="log-text">{@html formatMarkdown(part.content)}</div>
          </div>
        {/each}
      </div>
    </div>

    <!-- Bear Argument -->
    <div class="log-entry log-entry--bear">
      <div class="log-entry-header">
        <span class="log-agent-tag">[BEAR]</span>
        {#if round.score_delta < 0}
          <span class="log-delta neg">{round.score_delta} PTS</span>
        {/if}
      </div>
      <div class="log-entry-body">
        {#each splitArgumentByHeadings(round.bear_argument) as part}
          <div class="log-content-block">
            {#if part.title}
              <div class="log-block-title">{part.title}</div>
            {/if}
            <div class="log-text">{@html formatMarkdown(part.content)}</div>
          </div>
        {/each}
      </div>
    </div>
  </div>
{/snippet}

{#snippet finalVerdict(result: StockResult)}
  <div class="verdict-summary">
    <div class="verdict-header">
      <h3>Summary</h3>
      <div class="rating-badge" style="--rating-color: {ratingColor(result.rating)}">
        {result.rating.replace('_', ' ')}
      </div>
    </div>
    
    <div class="conviction-display">
      <div class="conviction-score">
        <strong>{result.conviction_score ?? 0}</strong><small>/100</small>
      </div>
      <div class="conviction-label">AI CONVICTION SCORE</div>
    </div>
    
    <div class="chat-bubbles-group verdict-bubbles">
      {#each splitArgumentByHeadings(result.verdict_summary) as part}
        <div class="verdict-text-box">
          {#if part.title}
            <div class="bubble-title">{part.title}</div>
          {/if}
          <div class="verdict-text">{@html formatMarkdown(part.content)}</div>
        </div>
      {/each}
    </div>

    {#if result.verdict_reasoning}
      <div class="verdict-reasoning">
        <h4>Thoughts & Reasoning</h4>
        <div class="chat-bubbles-group">
          {#each splitArgumentByHeadings(result.verdict_reasoning) as part}
            <div class="verdict-reasoning-box">
              {#if part.title}
                <div class="bubble-title">{part.title}</div>
              {/if}
              <div class="verdict-text">{@html formatMarkdown(part.content)}</div>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </div>
{/snippet}

<style>
  .debate-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .placeholder {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--sp-4);
    color: var(--text-secondary);
    text-align: center;
  }

  .placeholder__icon {
    font-size: 48px;
    opacity: 0.5;
  }

  .placeholder__text {
    font-size: 16px;
    line-height: 1.5;
  }

  .debate-header {
    border-bottom: 1px solid var(--surface-border);
    padding: var(--sp-4);
    background: rgba(0, 0, 0, 0.2);
  }

  .header-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: var(--sp-4);
  }

  .ticker-info h2 {
    margin: 0 0 var(--sp-1) 0;
    font-size: 28px;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: var(--text-primary);
  }

  .ticker-info p {
    margin: 0;
    font-size: 14px;
    color: var(--text-secondary);
  }

  .rating-badge {
    padding: 6px 12px;
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--rating-color) 15%, transparent);
    color: var(--rating-color);
    border: 1px solid color-mix(in srgb, var(--rating-color) 40%, transparent);
    font-weight: 700;
    font-size: 14px;
  }

  .price-highlights {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--sp-3);
  }

  .price-box {
    background: var(--surface-1);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: var(--sp-3);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .price-label {
    font-size: 11px;
    color: var(--text-secondary);
    text-transform: uppercase;
    font-weight: 600;
    letter-spacing: 0.05em;
  }

  .price-value {
    font-size: 16px;
  }

  .devil-advocate-status-bar {
    margin: var(--sp-4) var(--sp-4) 0 var(--sp-4);
    padding: var(--sp-2) var(--sp-4);
    border: 1px solid var(--accent-cyan);
    border-radius: var(--radius-sm);
    background: var(--accent-cyan-dim);
    color: var(--accent-cyan);
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.5px;
  }

  .status-indicator-dot {
    width: 6px;
    height: 6px;
    background-color: var(--accent-cyan);
    border-radius: 50%;
    display: inline-block;
    animation: pulse-glow 1.5s infinite;
  }

  @keyframes pulse-glow {
    0% { opacity: 0.3; }
    50% { opacity: 1; }
    100% { opacity: 0.3; }
  }

  .timeline-container {
    flex: 1;
    overflow-y: auto;
    padding: var(--sp-4);
    display: flex;
    flex-direction: column;
    gap: var(--sp-5);
  }

  .terminal-progress {
    background: rgba(0, 0, 0, 0.2);
    border: 1px dashed var(--surface-border);
    padding: var(--sp-3) var(--sp-4);
    border-radius: var(--radius-sm);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .terminal-progress-text {
    display: flex;
    justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.5px;
  }

  .mono-label {
    color: var(--text-secondary);
  }

  .mono-pct {
    color: var(--accent-cyan);
    font-weight: 700;
  }

  .terminal-progress-bar-text {
    font-family: var(--font-mono);
    font-size: 12px;
    letter-spacing: 2px;
    color: var(--text-muted);
  }

  .scout-summary-terminal {
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }

  .scout-title-bar {
    background: rgba(255, 255, 255, 0.02);
    padding: var(--sp-2) var(--sp-4);
    border-bottom: 1px solid var(--surface-border);
  }

  .scout-title-tag {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--text-secondary);
    letter-spacing: 0.1em;
  }

  .scout-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
  }

  .scout-column {
    padding: var(--sp-4);
    border-right: 1px solid var(--surface-border);
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .scout-column:last-child {
    border-right: none;
  }

  .scout-col-header {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    color: var(--text-muted);
    letter-spacing: 0.1em;
    border-bottom: 1px solid var(--surface-border);
    padding-bottom: 4px;
    margin-bottom: 4px;
  }

  .scout-metrics-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .scout-metric-item {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
  }

  .metric-key {
    color: var(--text-secondary);
  }

  .metric-val {
    color: var(--text-primary);
    font-weight: 600;
  }

  .terminal-log-round {
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
    width: 100%;
  }

  .log-round-divider {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    margin: var(--sp-2) 0;
  }

  .log-round-tag {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    color: var(--text-secondary);
    letter-spacing: 0.1em;
    padding: 2px 8px;
    border: 1px dashed var(--surface-border);
    border-radius: var(--radius-sm);
    background: rgba(255, 255, 255, 0.02);
  }

  .log-round-line {
    flex: 1;
    height: 1px;
    border-top: 1px dashed var(--surface-border);
  }

  .log-entry {
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }

  .log-entry--bull {
    border-left: 3px solid var(--signal-bull);
  }

  .log-entry--bear {
    border-left: 3px solid var(--signal-bear);
  }

  .log-entry-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: rgba(255, 255, 255, 0.02);
    padding: var(--sp-2) var(--sp-4);
    border-bottom: 1px solid var(--surface-border);
  }

  .log-agent-tag {
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.05em;
  }

  .log-entry--bull .log-agent-tag {
    color: var(--signal-bull);
  }

  .log-entry--bear .log-agent-tag {
    color: var(--signal-bear);
  }

  .log-delta {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: var(--radius-sm);
  }

  .log-delta.pos {
    background: var(--signal-bull-dim);
    color: var(--signal-bull);
  }

  .log-delta.neg {
    background: var(--signal-bear-dim);
    color: var(--signal-bear);
  }

  .log-entry-body {
    padding: var(--sp-4);
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
  }

  .log-content-block {
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .log-block-title {
    font-family: var(--font-sans);
    font-weight: 700;
    font-size: 14px;
    color: var(--text-primary);
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    padding-bottom: var(--sp-1);
    margin-bottom: var(--sp-1);
  }

  .log-text {
    font-size: 14px;
    line-height: 1.6;
    color: var(--text-secondary);
  }

  .log-text :global(strong) {
    color: var(--text-primary);
    font-weight: 600;
  }

  .log-text :global(p) {
    margin: 0 0 var(--sp-2) 0;
  }

  .log-text :global(p:last-child) {
    margin: 0;
  }

  .log-text :global(ul) {
    margin: var(--sp-2) 0;
    padding-left: var(--sp-5);
  }

  .devil-advocate-panel {
    background: rgba(255, 90, 0, 0.02);
    border: 1px solid var(--accent-cyan);
    border-left: 6px solid var(--accent-cyan);
    padding: var(--sp-4);
    border-radius: var(--radius-sm);
    position: relative;
    overflow: hidden;
  }

  .devil-advocate-panel::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4px;
    background: repeating-linear-gradient(
      -45deg,
      var(--accent-cyan),
      var(--accent-cyan) 10px,
      #000 10px,
      #000 20px
    );
  }

  .devil-advocate-header {
    margin-bottom: var(--sp-2);
    margin-top: 4px;
  }

  .devil-advocate-tag {
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 12px;
    color: var(--accent-cyan);
    letter-spacing: 0.05em;
  }

  .devil-advocate-body {
    font-size: 14px;
    line-height: 1.6;
    color: var(--text-secondary);
  }

  .devil-question-box {
    margin-top: var(--sp-3);
    padding: var(--sp-3);
    background: rgba(0, 0, 0, 0.3);
    border: 1px dashed var(--accent-cyan);
    border-radius: var(--radius-sm);
  }

  .devil-question-label {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--accent-cyan);
    margin-bottom: var(--sp-1);
    letter-spacing: 0.5px;
  }

  .devil-question-content {
    font-family: var(--font-sans);
    color: var(--text-primary);
  }

  .verdict-summary {
    background: linear-gradient(145deg, var(--surface-2), var(--surface-1));
    border: 1px solid var(--accent-cyan);
    border-radius: var(--radius-lg);
    padding: var(--sp-5);
    margin-top: var(--sp-4);
  }

  .verdict-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--sp-4);
  }

  .verdict-header h3 {
    margin: 0;
    color: var(--accent-cyan);
  }

  .conviction-display {
    text-align: center;
    margin-bottom: var(--sp-4);
    padding: var(--sp-3);
    background: rgba(0,0,0,0.2);
    border-radius: var(--radius-md);
  }

  .conviction-score strong {
    font-size: 42px;
    font-family: var(--font-mono);
    line-height: 1;
  }

  .conviction-label {
    font-size: 12px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 4px;
  }

  .verdict-text {
    font-size: 15px;
    line-height: 1.6;
  }

  .verdict-bubbles {
    margin-top: 1rem;
  }

  .verdict-text-box {
    background: rgba(255, 255, 255, 0.03);
    padding: var(--sp-3) var(--sp-4);
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.05);
  }

  .verdict-reasoning {
    margin-top: 1.5rem;
    padding: 1rem;
    background: rgba(255, 255, 255, 0.02);
    border-radius: 8px;
    border-left: 3px solid var(--accent-blue);
  }

  .verdict-reasoning h4 {
    margin: 0 0 1rem 0;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-secondary);
    opacity: 0.8;
  }

  .verdict-reasoning-box {
    background: rgba(0, 0, 0, 0.2);
    padding: var(--sp-3) var(--sp-4);
    border-radius: 8px;
  }

  .bubble-title {
    font-size: 13px;
    font-weight: 700;
    color: var(--accent-cyan);
    margin-bottom: 6px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    padding-bottom: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .chat-bubbles-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 100%;
  }

  .status-message {
    font-family: var(--font-mono);
    font-size: 12px;
    letter-spacing: 0.5px;
    padding: var(--sp-3) var(--sp-4);
    border-radius: var(--radius-sm);
    text-transform: uppercase;
    text-align: center;
  }

  .status-message.success {
    background: var(--signal-bull-dim);
    color: var(--signal-bull);
    border: 1px solid var(--signal-bull);
  }

  .status-message.error {
    background: var(--signal-bear-dim);
    color: var(--signal-bear);
    border: 1px solid var(--signal-bear);
  }

  .terminal-cursor-indicator {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    padding: var(--sp-2) var(--sp-4);
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-muted);
  }

  .blinking-block {
    color: var(--accent-cyan);
    animation: blink-cursor 1s step-end infinite;
  }

  @keyframes blink-cursor {
    from, to { color: transparent; }
    50% { color: var(--accent-cyan); }
  }

  .indicator-text {
    letter-spacing: 0.5px;
  }

  @media (max-width: 1024px) {
    .scout-grid {
      grid-template-columns: 1fr;
    }
    .scout-column {
      border-right: none;
      border-bottom: 1px solid var(--surface-border);
    }
    .scout-column:last-child {
      border-bottom: none;
    }
  }

  @media (max-width: 768px) {
    .price-highlights {
      grid-template-columns: 1fr;
    }
  }
</style>
