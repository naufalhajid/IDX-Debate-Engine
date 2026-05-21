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
    if (!keyInput.startsWith('AIza')) {
      toast('error', 'Format API Key tidak valid');
      return;
    }
    validating = true;
    try {
      apiKey.set(keyInput);
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
  <header class="sidebar__header">
    <div class="logo">
      <span class="logo__mark">IDX</span>
      <div>
        <div class="logo__title">IDX Analyst</div>
        <div class="logo__sub">Fundamental v2</div>
      </div>
    </div>
    <div
      class="conn-dot"
      class:conn-dot--online={serverOnline}
      title={serverOnline ? 'Server Online' : 'Server Offline'}
    ></div>
  </header>

  <div class="stats-bar">
    <div class="stat">
      <span class="stat__label">Strong Buy</span>
      <span class="stat__val stat__val--bull">{$summaryStats.strongBuy}</span>
    </div>
    <div class="stat">
      <span class="stat__label">Buy</span>
      <span class="stat__val stat__val--bull">{$summaryStats.buy}</span>
    </div>
    <div class="stat">
      <span class="stat__label">Avoid</span>
      <span class="stat__val stat__val--bear">{$summaryStats.avoid}</span>
    </div>
    <div class="stat">
      <span class="stat__label">Avg Conv.</span>
      <span class="stat__val">{$summaryStats.avgConviction}</span>
    </div>
  </div>

  <section class="sidebar__section">
    <h3 class="sidebar__label">Settings</h3>

    <label class="field-label" for="api-key">Gemini API Key</label>
    <div class="key-row">
      <input
        id="api-key"
        class="input"
        type={showKey ? 'text' : 'password'}
        placeholder="AIza..."
        bind:value={keyInput}
        onkeydown={(event) => event.key === 'Enter' && saveKey()}
      />
      <button
        class="btn-icon"
        onclick={() => (showKey = !showKey)}
        title="Toggle visibility"
        type="button"
      >
        {showKey ? 'Hide' : 'Show'}
      </button>
    </div>
    <button class="btn btn--primary" onclick={saveKey} disabled={validating} type="button">
      {validating ? 'Validating' : 'Save Key'}
    </button>

    <label class="field-label field-label--spaced" for="equity">Portfolio Equity</label>
    <input
      id="equity"
      class="input mono"
      type="number"
      step="1000000"
      bind:value={$portfolioEquity}
    />
  </section>

  <footer class="sidebar__footer">
    <span><kbd>K</kbd> Search</span>
    <span><kbd>D</kbd> Debate</span>
    <span><kbd>E</kbd> Export</span>
  </footer>
</aside>

<style>
  .sidebar {
    width: 248px;
    min-height: 100vh;
    position: sticky;
    top: 0;
    z-index: var(--z-sidebar);
    display: flex;
    flex-direction: column;
    gap: var(--sp-6);
    border-right: 1px solid var(--surface-border);
    padding: var(--sp-4);
    background: var(--surface-1);
  }

  .sidebar__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-4);
  }

  .logo {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    min-width: 0;
  }

  .logo__mark {
    width: 36px;
    height: 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--accent-cyan);
    border-radius: var(--radius-sm);
    color: var(--accent-cyan);
    background: var(--accent-cyan-dim);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
  }

  .logo__title {
    color: var(--text-primary);
    font-weight: 600;
  }

  .logo__sub {
    margin-top: 1px;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .conn-dot {
    width: 8px;
    height: 8px;
    flex: 0 0 auto;
    border-radius: 50%;
    background: var(--signal-bear);
    box-shadow: 0 0 8px var(--signal-bear);
  }

  .conn-dot--online {
    background: var(--signal-bull);
    box-shadow: 0 0 8px var(--signal-bull);
  }

  .stats-bar {
    display: grid;
    grid-template-columns: 1fr 1fr;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    overflow: hidden;
  }

  .stat {
    min-height: 58px;
    padding: var(--sp-3);
    border-right: 1px solid var(--surface-border);
    border-bottom: 1px solid var(--surface-border);
    background: var(--surface-base);
  }

  .stat:nth-child(2n) {
    border-right: 0;
  }

  .stat:nth-last-child(-n + 2) {
    border-bottom: 0;
  }

  .stat__label {
    display: block;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .stat__val {
    display: block;
    margin-top: var(--sp-2);
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 600;
  }

  .stat__val--bull {
    color: var(--signal-bull);
  }

  .stat__val--bear {
    color: var(--signal-bear);
  }

  .sidebar__section {
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .sidebar__label {
    margin: 0 0 var(--sp-2);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }

  .field-label {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .field-label--spaced {
    margin-top: var(--sp-4);
  }

  .key-row {
    display: grid;
    grid-template-columns: 1fr 44px;
    gap: var(--sp-2);
  }

  .sidebar__footer {
    margin-top: auto;
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
  }
</style>
