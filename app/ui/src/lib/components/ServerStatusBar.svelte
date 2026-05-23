<script lang="ts">
  import { summaryStats, debateStats } from '$lib/stores/dashboard';

  export let online = false;
  export let loading = false;
  export let lastUpdated: Date | null = null;
  export let latestDebateDate: string | null = null;

  function getDebateDateInfo(dateStr: string | null) {
    if (!dateStr) return { label: 'LATEST DEBATE', value: 'None', isRecent: null };
    const date = new Date(dateStr);
    const now = new Date();
    const oneMonthAgo = new Date();
    oneMonthAgo.setMonth(now.getMonth() - 1);
    
    const isRecent = date > oneMonthAgo;
    
    return {
      label: 'LATEST DEBATE',
      value: date.toLocaleDateString(),
      isRecent
    };
  }

  $: debateInfo = getDebateDateInfo(latestDebateDate);
  $: marketCards = [
    { label: 'STRONG BUY RECS', value: String($summaryStats.strongBuy), change: 'Live', positive: true, valueClass: '' },
    { label: 'AVG CONVICTION', value: String($summaryStats.avgConviction), change: 'Score', positive: null, valueClass: '' },
    { 
      label: 'TOTAL DEBATES', 
      value: $debateStats ? String($debateStats.total_debates) : '0', 
      change: 'Debated', 
      positive: null, 
      valueClass: '' 
    },
    { 
      label: 'CONSENSUS RATE', 
      value: $debateStats ? `${$debateStats.consensus_rate}%` : '0%', 
      change: 'Rate', 
      positive: $debateStats && $debateStats.consensus_rate > 50 ? true : null, 
      valueClass: '' 
    },
    { 
      label: debateInfo.label, 
      value: debateInfo.value, 
      change: debateInfo.isRecent === true ? 'Fresh' : debateInfo.isRecent === false ? 'Stale' : 'N/A', 
      positive: debateInfo.isRecent,
      valueClass: debateInfo.isRecent === true ? 'text-green' : debateInfo.isRecent === false ? 'text-red' : ''
    }
  ];
</script>

<header class="top-header">
  <div class="header-title">
    <h1>Dashboard</h1>
    <span class="subtitle">Overview and Active Recommendations</span>
  </div>

  <div class="market-overview">
    {#each marketCards as item}
      <div class="market-card">
        <div class="card-info">
          <span class="card-label">{item.label}</span>
          <strong class="card-value {item.valueClass}">{item.value}</strong>
        </div>
        <div class="card-badge" class:pos={item.positive === true} class:neg={item.positive === false} class:neutral={item.positive === null}>
          {item.change}
        </div>
      </div>
    {/each}
  </div>

  <div class="user-actions">
    <div class="api-status {online ? 'online' : 'offline'}">
      {#if loading}
        <span class="spinner"></span>
      {:else}
        <span class="pulse"></span>
      {/if}
      <div class="status-details">
        <span class="status-text">{online ? 'API Connected' : 'API Disconnected'}</span>
        {#if lastUpdated}
          <span class="last-updated">Updated: {lastUpdated.toLocaleTimeString()}</span>
        {/if}
      </div>
    </div>
    
    <button class="action-btn" title="Notifications">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
      <span class="badge-dot"></span>
    </button>

    <div class="user-avatar">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
    </div>
  </div>
</header>

<style>
  .text-green { color: var(--signal-bull) !important; }
  .text-red { color: var(--signal-bear) !important; }

  .status-details {
    display: flex;
    flex-direction: column;
    gap: 1px;
    align-items: flex-start;
  }

  .status-text {
    line-height: 1.2;
  }

  .last-updated {
    font-size: 9px;
    color: var(--text-muted);
    font-weight: 500;
    line-height: 1;
  }

  .spinner {
    width: 8px;
    height: 8px;
    border: 1.5px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .top-header {
    min-height: 80px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--sp-3) var(--sp-4);
    border-bottom: 1px solid var(--surface-border);
    background: transparent;
    gap: var(--sp-4);
  }

  .header-title h1 {
    margin: 0;
    font-size: 24px;
    font-weight: 800;
    color: var(--text-primary);
  }

  .header-title .subtitle {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .market-overview {
    display: flex;
    gap: var(--sp-3);
    flex: 1;
    justify-content: center;
  }

  .market-card {
    background: var(--surface-1);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    padding: var(--sp-2) var(--sp-3);
    display: flex;
    align-items: center;
    gap: var(--sp-4);
    min-width: 160px;
  }

  .card-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .card-label {
    font-size: 11px;
    color: var(--text-secondary);
    font-weight: 600;
    text-transform: uppercase;
  }

  .card-value {
    font-size: 16px;
    font-family: var(--font-mono);
    color: var(--text-primary);
  }

  .card-badge {
    font-size: 11px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    margin-left: auto;
  }

  .card-badge.pos { background: var(--signal-bull-dim); color: var(--signal-bull); }
  .card-badge.neg { background: var(--signal-bear-dim); color: var(--signal-bear); }
  .card-badge.neutral { background: var(--surface-3); color: var(--text-primary); }

  .user-actions {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
  }

  .api-status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 12px;
    border-radius: 999px;
    background: var(--surface-1);
    border: 1px solid var(--surface-border);
  }

  .api-status.online { color: var(--signal-bull); }
  .api-status.offline { color: var(--signal-bear); }

  .pulse {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 8px currentColor;
  }

  .action-btn {
    position: relative;
    background: var(--surface-1);
    border: 1px solid var(--surface-border);
    color: var(--text-secondary);
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
  }

  .action-btn:hover {
    color: var(--text-primary);
    background: var(--surface-2);
  }

  .badge-dot {
    position: absolute;
    top: 10px;
    right: 10px;
    width: 8px;
    height: 8px;
    background: var(--accent-red);
    border-radius: 50%;
    border: 2px solid var(--surface-1);
  }

  .user-avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: linear-gradient(135deg, var(--accent-cyan), var(--accent-violet));
    display: flex;
    align-items: center;
    justify-content: center;
    color: #fff;
    box-shadow: var(--shadow-sm);
  }

  @media (max-width: 1280px) {
    .market-overview {
      display: none;
    }
  }
</style>
