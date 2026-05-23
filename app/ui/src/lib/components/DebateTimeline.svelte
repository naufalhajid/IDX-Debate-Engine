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

  function formatIdr(value: number) {
    return value.toLocaleString('id-ID');
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
      <div class="placeholder__text">Pilih saham dari daftar Rekomendasi<br/>untuk melihat Analisis & Ruang Debat AI</div>
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
            <strong class="price-value">Rp {formatIdr(activeResult.entry_low)}</strong>
          </div>
          <div class="price-box">
            <span class="price-label">Target Price</span>
            <strong class="price-value signal-bull">Rp {formatIdr(activeResult.target_price)}</strong>
          </div>
          <div class="price-box">
            <span class="price-label">Stop Loss</span>
            <strong class="price-value signal-bear">Rp {formatIdr(activeResult.stop_loss)}</strong>
          </div>
        </div>
      {/if}
    </header>

    {#if activeResult?.devil_advocate_triggered && !hasDevilEvent}
      <div class="alert-banner">
        <div class="alert-icon">!</div>
        <div class="alert-content">
          <strong>Devil's Advocate Aktif</strong>
          <span>Konsensus diuji ulang untuk memastikan ketahanan argumen AI.</span>
        </div>
      </div>
    {/if}

    <div class="timeline-container" bind:this={timelineEl} onscroll={onScroll}>
      {#each events as event, index (`${event.type}-${event.ticker}-${index}`)}
        {#if event.type === 'progress'}
          <div class="progress-indicator">
            <div class="progress-text">
              <span>{formatKey(event.phase)}...</span>
              <span>{pct(event.pct)}%</span>
            </div>
            <div class="progress-bar">
              <span style="width: {pct(event.pct)}%"></span>
            </div>
          </div>

        {:else if event.type === 'scout'}
          <div class="scout-summary">
            <div class="scout-title">Ringkasan Scout</div>
            <div class="scout-cards">
              {#each scoutCategories as category (category.key)}
                <div class="scout-metric-card">
                  <h4>{category.label}</h4>
                  {#each Object.entries(event.metrics[category.key]).slice(0, 3) as [key, value] (key)}
                    <div class="metric-row">
                      <span class="metric-key">{formatKey(key)}</span>
                      <span class="metric-val">{value}</span>
                    </div>
                  {/each}
                </div>
              {/each}
            </div>
          </div>

        {:else if event.type === 'round'}
          {@render roundBubble(event.data)}

        {:else if event.type === 'devil_advocate'}
          <div class="alert-banner alert-warning">
            <div class="alert-icon">⚠</div>
            <div class="alert-content">
              <strong>Devil's Advocate Memicu!</strong>
              <span>Konsensus tercapai terlalu cepat — AI dipaksa berdebat lebih keras.</span>
              {#if event.question}
                <div class="devil-question">
                  <strong>Pertanyaan:</strong> {@html formatMarkdown(event.question)}
                </div>
              {/if}
            </div>
          </div>

        {:else if event.type === 'verdict'}
          {@render finalVerdict(event.result)}

        {:else if event.type === 'done'}
          <div class="status-message success">✓ Analisis selesai</div>

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
        <div class="typing-indicator">
          <span></span><span></span><span></span>
        </div>
      {/if}
    </div>
  {/if}
</div>

{#snippet roundBubble(round: DebateRound)}
  <div class="chat-thread">
    <div class="chat-time">Babak {round.round}</div>
    
    <div class="chat-message bull-msg">
      <div class="chat-avatar">🐂</div>
      <div class="chat-bubbles-group">
        <div class="chat-name">Bull AI {#if round.score_delta > 0}<span class="delta pos">+{round.score_delta}</span>{/if}</div>
        {#each splitArgumentByHeadings(round.bull_argument) as part}
          <div class="chat-bubble">
            {#if part.title}
              <div class="bubble-title">{part.title}</div>
            {/if}
            <div class="chat-text">{@html formatMarkdown(part.content)}</div>
          </div>
        {/each}
      </div>
    </div>

    <div class="chat-message bear-msg">
      <div class="chat-avatar">🐻</div>
      <div class="chat-bubbles-group">
        <div class="chat-name">Bear AI {#if round.score_delta < 0}<span class="delta neg">{round.score_delta}</span>{/if}</div>
        {#each splitArgumentByHeadings(round.bear_argument) as part}
          <div class="chat-bubble">
            {#if part.title}
              <div class="bubble-title">{part.title}</div>
            {/if}
            <div class="chat-text">{@html formatMarkdown(part.content)}</div>
          </div>
        {/each}
      </div>
    </div>
  </div>
{/snippet}

{#snippet finalVerdict(result: StockResult)}
  <div class="verdict-summary">
    <div class="verdict-header">
      <h3>Kesimpulan Akhir</h3>
      <div class="rating-badge" style="--rating-color: {ratingColor(result.rating)}">
        {result.rating.replace('_', ' ')}
      </div>
    </div>
    
    <div class="conviction-display">
      <div class="conviction-score">
        <strong>{result.conviction_score}</strong><small>/100</small>
      </div>
      <div class="conviction-label">Skor Keyakinan AI</div>
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
        <h4>Pemikiran & Alasan</h4>
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
    border-radius: var(--radius-md);
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
    font-family: var(--font-mono);
  }

  .alert-banner {
    margin: var(--sp-4);
    padding: var(--sp-3);
    border-radius: var(--radius-md);
    background: var(--surface-2);
    border-left: 4px solid var(--accent-violet);
    display: flex;
    gap: var(--sp-3);
    align-items: center;
  }

  .alert-warning {
    border-left-color: var(--accent-gold);
    background: rgba(240, 180, 41, 0.1);
  }

  .alert-icon {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.1);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: bold;
  }

  .alert-content strong {
    display: block;
    font-size: 14px;
    margin-bottom: 2px;
  }

  .alert-content span { opacity: 0.9; }

  .devil-question {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed rgba(255, 255, 255, 0.2);
    font-size: 14px;
    font-style: italic;
  }



  .timeline-container {
    flex: 1;
    overflow-y: auto;
    padding: var(--sp-4);
    display: flex;
    flex-direction: column;
    gap: var(--sp-5);
  }

  .progress-indicator {
    background: var(--surface-1);
    padding: var(--sp-3);
    border-radius: var(--radius-md);
    border: 1px solid var(--surface-border);
  }

  .progress-text {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: var(--sp-2);
  }

  .progress-bar {
    height: 4px;
    background: var(--surface-3);
    border-radius: 999px;
    overflow: hidden;
  }

  .progress-bar span {
    display: block;
    height: 100%;
    background: var(--accent-cyan);
    transition: width 0.3s ease;
  }

  .scout-summary {
    background: var(--surface-1);
    border-radius: var(--radius-md);
    border: 1px solid var(--surface-border);
    padding: var(--sp-4);
  }

  .scout-title {
    font-weight: 700;
    color: var(--accent-cyan);
    margin-bottom: var(--sp-3);
  }

  .scout-cards {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--sp-3);
  }

  .scout-metric-card h4 {
    margin: 0 0 var(--sp-2) 0;
    font-size: 12px;
    color: var(--text-secondary);
    text-transform: uppercase;
  }

  .metric-row {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin-bottom: 4px;
  }

  .metric-key { color: var(--text-muted); }
  .metric-val { color: var(--text-primary); }

  .chat-thread {
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
  }

  .chat-time {
    text-align: center;
    font-size: 12px;
    color: var(--text-muted);
    margin: var(--sp-2) 0;
  }

  .chat-message {
    display: flex;
    gap: var(--sp-3);
    max-width: 90%;
  }

  .bear-msg {
    align-self: flex-end;
    flex-direction: row-reverse;
  }

  .chat-avatar {
    font-size: 24px;
    line-height: 1;
    margin-top: 4px;
  }

  .chat-bubble {
    background: var(--surface-2);
    padding: var(--sp-3) var(--sp-4);
    border-radius: 16px;
    border-top-left-radius: 4px;
    box-shadow: var(--shadow-sm);
  }

  .bear-msg .chat-bubble {
    background: var(--surface-1);
    border-top-left-radius: 16px;
    border-top-right-radius: 4px;
    border: 1px solid var(--surface-border);
  }

  .chat-bubbles-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 100%;
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

  .bear-msg .bubble-title {
    color: var(--accent-orange);
  }

  .chat-name {
    font-size: 12px;
    font-weight: 700;
    color: var(--text-secondary);
    margin-bottom: var(--sp-2);
  }

  .delta {
    margin-left: 6px;
    font-weight: bold;
  }

  .delta.pos { color: var(--signal-bull); }
  .delta.neg { color: var(--signal-bear); }

  .chat-text {
    font-size: 14px;
    line-height: 1.6;
    color: var(--text-primary);
  }

  .verdict-text {
    font-size: 0.95rem;
    line-height: 1.6;
    color: var(--text-color);
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
    color: var(--text-color);
    opacity: 0.8;
  }

  .verdict-reasoning-box {
    background: rgba(0, 0, 0, 0.2);
    padding: var(--sp-3) var(--sp-4);
    border-radius: 8px;
  }


  .chat-text :global(p) { margin: 0 0 10px 0; }
  .chat-text :global(p:last-child) { margin: 0; }
  .chat-text :global(ul) { margin: 4px 0; padding-left: 20px; }
  .chat-text :global(strong) { color: var(--accent-cyan); }

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

  .status-message {
    text-align: center;
    font-size: 14px;
    padding: var(--sp-3);
    border-radius: var(--radius-md);
    background: var(--surface-1);
  }

  .status-message.success { color: var(--signal-bull); }
  .status-message.error { color: var(--signal-bear); border: 1px solid var(--signal-bear); }

  .typing-indicator {
    display: flex;
    gap: 6px;
    padding: var(--sp-3);
    align-self: flex-start;
  }

  .typing-indicator span {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-secondary);
    animation: bounce 1.4s infinite ease-in-out both;
  }

  .typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
  .typing-indicator span:nth-child(2) { animation-delay: -0.16s; }

  @keyframes bounce {
    0%, 80%, 100% { transform: scale(0); }
    40% { transform: scale(1); }
  }

  @media (max-width: 768px) {
    .scout-cards, .price-highlights {
      grid-template-columns: 1fr;
    }
    .chat-message { max-width: 100%; }
  }
</style>
