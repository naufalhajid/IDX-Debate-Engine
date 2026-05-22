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
    { label: 'Analytics', icon: 'bars', active: false }
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
    // Clean key input from potential copy-paste spaces/quotes
    const cleanedKey = keyInput.trim().replace(/^['"]|['"]$/g, '');
    keyInput = cleanedKey; // Update input field value
    
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
    <span class="brand__mark"></span>
    <div class="brand__copy">
      <strong>IDX DEBATE</strong>
      <strong>ENGINE</strong>
    </div>
    <span
      class="brand__status"
      class:brand__status--online={serverOnline}
      title={serverOnline ? 'Server Online' : 'Server Offline'}
    ></span>
  </header>

  <nav class="nav-list" aria-label="Primary navigation">
    {#each navItems as item}
      <button class="nav-item" class:nav-item--active={item.active} type="button">
        <span class="nav-icon nav-icon--{item.icon}"></span>
        <span>{item.label}</span>
      </button>
    {/each}
  </nav>

  <div class="side-metrics">
    <div>
      <span>BUY</span>
      <strong>{$summaryStats.buy + $summaryStats.strongBuy}</strong>
    </div>
    <div>
      <span>AVOID</span>
      <strong class="bear">{$summaryStats.avoid}</strong>
    </div>
  </div>

  <section class="key-panel" aria-label="Settings">
    <div class="key-panel__title">Settings</div>
    <label class="field-label" for="api-key">Gemini Key</label>
    <div class="key-row">
      <input
        id="api-key"
        class="input"
        type={showKey ? 'text' : 'password'}
        placeholder="API Key..."
        bind:value={keyInput}
        onkeydown={(event) => event.key === 'Enter' && saveKey()}
      />
      <button
        class="btn-icon"
        onclick={() => (showKey = !showKey)}
        title="Toggle visibility"
        type="button"
      >
        {showKey ? 'H' : 'S'}
      </button>
    </div>
    <button class="btn btn--primary" onclick={saveKey} disabled={validating} type="button">
      {validating ? 'Check' : 'Save'}
    </button>

    <label class="field-label field-label--spaced" for="equity">Equity</label>
    <input
      id="equity"
      class="input mono"
      type="number"
      step="1000000"
      bind:value={$portfolioEquity}
    />
  </section>
</aside>

<style>
  .sidebar {
    width: 142px;
    min-height: 100%;
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
    border-right: 1px solid rgba(118, 139, 164, 0.13);
    padding: var(--sp-3) var(--sp-2);
    background: linear-gradient(180deg, rgba(15, 24, 35, 0.94), rgba(8, 14, 21, 0.96));
  }

  .brand {
    min-height: 54px;
    display: grid;
    grid-template-columns: 32px 1fr 10px;
    align-items: center;
    gap: var(--sp-2);
    padding: 0 var(--sp-1);
  }

  .brand__mark {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 1px solid rgba(255, 95, 102, 0.6);
    background:
      repeating-linear-gradient(135deg, rgba(255, 95, 102, 0.85) 0 2px, transparent 2px 5px),
      rgba(255, 95, 102, 0.13);
    box-shadow: 0 0 18px rgba(255, 95, 102, 0.18);
  }

  .brand__copy {
    min-width: 0;
    display: grid;
    line-height: 1.05;
  }

  .brand__copy strong {
    overflow: hidden;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 800;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .brand__status {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--signal-bear);
    box-shadow: 0 0 8px var(--signal-bear);
  }

  .brand__status--online {
    background: var(--signal-bull);
    box-shadow: 0 0 8px var(--signal-bull);
  }

  .nav-list {
    display: grid;
    gap: var(--sp-1);
  }

  .nav-item {
    position: relative;
    width: 100%;
    min-height: 32px;
    display: grid;
    grid-template-columns: 18px 1fr;
    align-items: center;
    gap: var(--sp-2);
    border: 0;
    border-radius: var(--radius-sm);
    padding: 0 var(--sp-2);
    background: transparent;
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
    text-align: left;
    text-transform: uppercase;
  }

  .nav-item--active {
    background: rgba(32, 208, 131, 0.13);
    color: var(--signal-bull);
  }

  .nav-item--active::before {
    content: '';
    position: absolute;
    left: -8px;
    top: 3px;
    bottom: 3px;
    width: 2px;
    border-radius: 999px;
    background: var(--signal-bull);
    box-shadow: 0 0 10px var(--signal-bull);
  }

  .nav-icon {
    position: relative;
    width: 13px;
    height: 13px;
    color: currentColor;
  }

  .nav-icon--grid {
    border: 1px solid currentColor;
    box-shadow: 6px 0 0 -1px currentColor, 0 6px 0 -1px currentColor, 6px 6px 0 -1px currentColor;
  }

  .nav-icon--trend::before {
    content: '';
    position: absolute;
    inset: 5px 0 3px;
    border-top: 2px solid currentColor;
    transform: skew(-35deg);
  }

  .nav-icon--star::before {
    content: '';
    position: absolute;
    inset: 1px;
    border: 1px solid currentColor;
    clip-path: polygon(50% 0, 62% 35%, 100% 38%, 70% 60%, 82% 100%, 50% 76%, 18% 100%, 30% 60%, 0 38%, 38% 35%);
  }

  .nav-icon--chat {
    border: 1px solid currentColor;
    border-radius: 3px;
  }

  .nav-icon--chat::after {
    content: '';
    position: absolute;
    left: 2px;
    bottom: -3px;
    width: 5px;
    height: 5px;
    border-left: 1px solid currentColor;
    border-bottom: 1px solid currentColor;
    background: transparent;
    transform: rotate(-12deg);
  }

  .nav-icon--bars::before {
    content: '';
    position: absolute;
    inset: 2px 8px 1px 1px;
    border-left: 2px solid currentColor;
    box-shadow: 5px 3px 0 0 currentColor, 10px -2px 0 0 currentColor;
  }

  .side-metrics {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--sp-1);
  }

  .side-metrics div {
    min-width: 0;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-sm);
    padding: var(--sp-2);
    background: rgba(4, 9, 14, 0.42);
  }

  .side-metrics span {
    display: block;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 8px;
    font-weight: 700;
  }

  .side-metrics strong {
    display: block;
    margin-top: 2px;
    color: var(--signal-bull);
    font-family: var(--font-mono);
    font-size: 14px;
  }

  .side-metrics .bear {
    color: var(--signal-bear);
  }

  .key-panel {
    margin-top: auto;
    display: grid;
    gap: var(--sp-2);
    border-top: 1px solid var(--surface-border);
    padding-top: var(--sp-3);
  }

  .key-panel__title,
  .field-label {
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
  }

  .field-label--spaced {
    margin-top: var(--sp-1);
  }

  .key-row {
    display: grid;
    grid-template-columns: 1fr 30px;
    gap: var(--sp-1);
  }

  @media (max-width: 900px) {
    .sidebar {
      width: 100%;
      min-height: auto;
      position: static;
    }

    .nav-list {
      grid-template-columns: repeat(5, minmax(0, 1fr));
    }

    .key-panel {
      grid-template-columns: 1fr 1fr;
      align-items: end;
    }
  }
</style>
