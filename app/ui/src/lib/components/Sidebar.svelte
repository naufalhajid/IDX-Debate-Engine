<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { api } from '$lib/clients/api';
  import { summaryStats, debateStats, activeTab } from '$lib/stores/dashboard';
  import { apiKey, portfolioEquity } from '$lib/stores/session';
  import { toast } from '$lib/stores/toast';
  export let width = 260;
  export let collapsed = false;

  let keyInput = '';
  let showKey = false;
  let serverOnline = false;
  let validating = false;

  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: 'grid' },
    { id: 'watchlist', label: 'Watchlist', icon: 'star' }
  ];

  onMount(async () => {
    keyInput = get(apiKey);
    try {
      await api.health();
      serverOnline = true;
    } catch {
      serverOnline = false;
    }
  });

  async function saveKey() {
    const cleanedKey = keyInput.trim().replace(/^['"]|['"]$/g, '');
    keyInput = cleanedKey;
    
    if (!cleanedKey) {
      toast('error', 'API Key cannot be empty');
      return;
    }
    validating = true;
    try {
      apiKey.set(cleanedKey);
      const result = await api.validateKey();
      if (!result.valid) {
        apiKey.clear();
        toast('error', 'API Key validation failed');
        return;
      }
      toast('success', 'API Key saved and validated');
    } catch (error: unknown) {
      toast('error', (error as Error).message);
    } finally {
      validating = false;
    }
  }
</script>

<aside class="sidebar" class:collapsed={collapsed} style="width: {width}px;">
  <header class="brand">
    <div class="brand-logo-container">
      <div class="brand-logo">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 3v18h18" />
          <path d="m19 9-5 5-4-4-3 3" />
        </svg>
      </div>
      {#if collapsed}
        <div class="status-indicator status-indicator--logo {serverOnline ? 'online' : 'offline'}" title={serverOnline ? 'Server Online' : 'Server Offline'}></div>
      {/if}
    </div>
    {#if !collapsed}
      <div class="brand-text">
        <h2>IDX DEBATE</h2>
        <span>Intelligence Engine</span>
      </div>
      <div class="status-indicator {serverOnline ? 'online' : 'offline'}" title={serverOnline ? 'Server Online' : 'Server Offline'}></div>
    {/if}
  </header>

  <nav class="navigation">
    {#each navItems as item}
      <button 
        class="nav-item {$activeTab === item.id ? 'active' : ''}" 
        onclick={() => activeTab.set(item.id as 'dashboard' | 'watchlist')}
        title={collapsed ? item.label : ''}
      >
        <div class="nav-icon">
          {#if item.icon === 'grid'}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
          {:else if item.icon === 'star'}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
          {/if}
        </div>
        {#if !collapsed}
          <span>{item.label}</span>
        {/if}
      </button>
    {/each}
  </nav>

  {#if !collapsed}
    <div class="metrics-cards">
      <div class="metric-card">
        <span class="metric-label">EXECUTABLE BUY</span>
        <strong class="metric-val signal-bull">
          {$debateStats ? ($debateStats.execution_status_distribution.EXECUTABLE_BUY || 0) : 0}
        </strong>
      </div>
      <div class="metric-card">
        <span class="metric-label">WAITLIST</span>
        <strong class="metric-val" style="color: var(--signal-hold);">
          {$debateStats ? ($debateStats.execution_status_distribution.WAITLIST || 0) : 0}
        </strong>
      </div>
      <div class="metric-card">
        <span class="metric-label">NOT EXECUTABLE</span>
        <strong class="metric-val signal-bear">
          {$debateStats
            ? ($debateStats.execution_status_distribution.NO_TRADE || 0) +
              ($debateStats.execution_status_distribution.AVOID || 0) +
              ($debateStats.execution_status_distribution.INSUFFICIENT_DATA || 0)
            : 0}
        </strong>
      </div>
      <div class="metric-card">
        <span class="metric-label">FRESH DEBATES</span>
        <strong class="metric-val signal-bull">
          {$debateStats ? $debateStats.fresh_count : 0}
        </strong>
      </div>
    </div>
  {/if}

  {#if !collapsed}
    <section class="settings-panel">
      <h3>Settings</h3>
      
      <div class="form-group">
        <label for="api-key">Gemini API Key</label>
        <div class="input-with-action">
          <input id="api-key" class="input" type={showKey ? 'text' : 'password'} placeholder="Enter API Key" bind:value={keyInput} onkeydown={(e) => e.key === 'Enter' && saveKey()} />
          <button class="icon-button" onclick={() => (showKey = !showKey)}>
            {#if showKey}
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            {:else}
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
            {/if}
          </button>
        </div>
        <button class="btn btn--primary full-width" onclick={saveKey} disabled={validating}>
          {validating ? 'Checking...' : 'Save Key'}
        </button>
      </div>

      <div class="form-group">
        <label for="equity">Simulated Equity</label>
        <input id="equity" class="input mono" type="number" step="1000000" bind:value={$portfolioEquity} />
      </div>
    </section>
  {/if}

  <button 
    class="collapse-toggle" 
    onclick={() => collapsed = !collapsed} 
    title={collapsed ? "Expand Sidebar" : "Collapse Sidebar"}
    type="button"
  >
    <div class="toggle-icon-container">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="toggle-icon">
        {#if collapsed}
          <polyline points="9 18 15 12 9 6" />
        {:else}
          <polyline points="15 18 9 12 15 6" />
        {/if}
      </svg>
    </div>
    {#if !collapsed}
      <span>Collapse Sidebar</span>
    {/if}
  </button>
</aside>

<style>
  .sidebar {
    background: var(--surface-1);
    display: flex;
    flex-direction: column;
    padding: var(--sp-4);
    gap: var(--sp-5);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding-bottom: var(--sp-4);
    border-bottom: 1px solid var(--surface-border);
  }

  .brand-logo {
    width: 36px;
    height: 36px;
    background: var(--accent-cyan-dim);
    color: var(--accent-cyan);
    border-radius: var(--radius-sm);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .brand-text h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 800;
    color: var(--text-primary);
  }

  .brand-text span {
    font-size: 11px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .status-indicator {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-left: auto;
  }
  .status-indicator.online { background: var(--signal-bull); box-shadow: 0 0 8px var(--signal-bull); }
  .status-indicator.offline { background: var(--signal-bear); }

  .navigation {
    display: flex;
    flex-direction: column;
    gap: var(--sp-1);
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-3);
    border-radius: var(--radius-md);
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-secondary);
    font-size: 14px;
    font-weight: 500;
    transition: all 0.2s;
  }

  .nav-item:hover {
    background: var(--surface-2);
    color: var(--text-primary);
  }

  .nav-item.active {
    background: var(--accent-cyan-dim);
    color: var(--accent-cyan);
    border-color: rgba(56, 189, 248, 0.3);
  }

  .nav-icon {
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .metrics-cards {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--sp-2);
  }

  .metric-card {
    background: var(--surface-2);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    padding: var(--sp-3);
    text-align: center;
  }

  .metric-label {
    display: block;
    font-size: 10px;
    color: var(--text-muted);
    font-weight: 700;
    margin-bottom: var(--sp-1);
  }

  .metric-val {
    font-size: 18px;
    font-family: var(--font-mono);
  }

  .settings-panel {
    margin-top: auto;
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
    background: var(--surface-2);
    border-radius: var(--radius-md);
    padding: var(--sp-4);
  }

  .settings-panel h3 {
    margin: 0 0 var(--sp-2) 0;
    font-size: 14px;
    color: var(--text-primary);
  }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: var(--sp-1);
  }

  .form-group label {
    font-size: 12px;
    color: var(--text-secondary);
  }

  .input-with-action {
    position: relative;
    display: flex;
    align-items: center;
  }

  .input-with-action input {
    padding-right: 40px;
  }

  .icon-button {
    position: absolute;
    right: 8px;
    background: transparent;
    border: none;
    color: var(--text-secondary);
    padding: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
  }

  .icon-button:hover {
    color: var(--text-primary);
    background: var(--surface-3);
  }

  .full-width {
    width: 100%;
    margin-top: var(--sp-2);
  }

  /* Collapsed aside layout */
  .sidebar.collapsed {
    padding: var(--sp-4) var(--sp-2);
    align-items: center;
    gap: var(--sp-6);
  }

  .brand-logo-container {
    position: relative;
    display: inline-block;
  }

  .status-indicator--logo {
    position: absolute;
    bottom: -2px;
    right: -2px;
    border: 2px solid var(--surface-1);
    width: 10px;
    height: 10px;
    margin-left: 0;
  }

  .sidebar.collapsed .navigation {
    align-items: center;
    width: 100%;
  }

  .sidebar.collapsed .nav-item {
    justify-content: center;
    width: 48px;
    height: 48px;
    padding: 0;
  }

  /* Collapse Toggle button styles */
  .collapse-toggle {
    margin-top: auto;
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-3);
    background: transparent;
    border: 1px dashed var(--surface-border);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    width: 100%;
  }

  .sidebar.collapsed .collapse-toggle {
    border: none;
    padding: 0;
    width: 48px;
    height: 48px;
    justify-content: center;
  }

  .collapse-toggle:hover {
    color: var(--text-primary);
    background: var(--surface-2);
    border-color: var(--surface-border-strong);
  }

  .toggle-icon-container {
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .toggle-icon {
    transition: transform 0.2s;
  }

  @media (max-width: 1080px) {
    .sidebar {
      width: 100% !important;
      border-right: none;
      border-bottom: 1px solid var(--surface-border);
    }
    
    .navigation {
      flex-direction: row;
      flex-wrap: wrap;
    }
  }
</style>
