<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { api } from '$lib/clients/api';
  import { summaryStats } from '$lib/stores/dashboard';
  import { apiKey, portfolioEquity } from '$lib/stores/session';
  import { toast } from '$lib/stores/toast';

  let keyInput = '';
  let showKey = false;
  let serverOnline = false;
  let validating = false;

  const navItems = [
    { label: 'Dashboard', icon: 'grid', active: true },
    { label: 'Markets', icon: 'trend', active: false },
    { label: 'Watchlists', icon: 'star', active: false },
    { label: 'Debate Hub', icon: 'chat', active: false },
    { label: 'Analytics', icon: 'chart', active: false }
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
      toast('error', 'API Key tidak boleh kosong');
      return;
    }
    validating = true;
    try {
      apiKey.set(cleanedKey);
      await api.validateKey();
      toast('success', 'API Key tersimpan dan valid');
    } catch (error: unknown) {
      toast('error', (error as Error).message);
    } finally {
      validating = false;
    }
  }
</script>

<aside class="sidebar">
  <header class="brand">
    <div class="brand-logo">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 3v18h18" />
        <path d="m19 9-5 5-4-4-3 3" />
      </svg>
    </div>
    <div class="brand-text">
      <h2>IDX DEBATE</h2>
      <span>Intelligence Engine</span>
    </div>
    <div class="status-indicator {serverOnline ? 'online' : 'offline'}" title={serverOnline ? 'Server Online' : 'Server Offline'}></div>
  </header>

  <nav class="navigation">
    {#each navItems as item}
      <button class="nav-item {item.active ? 'active' : ''}">
        <div class="nav-icon">
          {#if item.icon === 'grid'}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
          {:else if item.icon === 'trend'}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
          {:else if item.icon === 'star'}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
          {:else if item.icon === 'chat'}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          {:else if item.icon === 'chart'}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          {/if}
        </div>
        <span>{item.label}</span>
      </button>
    {/each}
  </nav>

  <div class="metrics-cards">
    <div class="metric-card">
      <span class="metric-label">BUY SIGNALS</span>
      <strong class="metric-val signal-bull">{$summaryStats.buy + $summaryStats.strongBuy}</strong>
    </div>
    <div class="metric-card">
      <span class="metric-label">AVOID SIGNALS</span>
      <strong class="metric-val signal-bear">{$summaryStats.avoid}</strong>
    </div>
  </div>

  <section class="settings-panel">
    <h3>Pengaturan</h3>
    
    <div class="form-group">
      <label for="api-key">Gemini API Key</label>
      <div class="input-with-action">
        <input id="api-key" class="input" type={showKey ? 'text' : 'password'} placeholder="Masukkan API Key" bind:value={keyInput} onkeydown={(e) => e.key === 'Enter' && saveKey()} />
        <button class="icon-button" onclick={() => (showKey = !showKey)}>
          {#if showKey}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
          {:else}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
          {/if}
        </button>
      </div>
      <button class="btn btn--primary full-width" onclick={saveKey} disabled={validating}>
        {validating ? 'Memeriksa...' : 'Simpan Key'}
      </button>
    </div>

    <div class="form-group">
      <label for="equity">Modal Simulasi (Equity)</label>
      <input id="equity" class="input mono" type="number" step="1000000" bind:value={$portfolioEquity} />
    </div>
  </section>
</aside>

<style>
  .sidebar {
    width: 260px;
    background: var(--surface-1);
    border-right: 1px solid var(--surface-border);
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

  @media (max-width: 1080px) {
    .sidebar {
      width: 100%;
      border-right: none;
      border-bottom: 1px solid var(--surface-border);
    }
    
    .navigation {
      flex-direction: row;
      flex-wrap: wrap;
    }
  }
</style>
