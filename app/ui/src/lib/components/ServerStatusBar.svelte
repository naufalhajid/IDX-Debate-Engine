<script lang="ts">
  export let online = false;
  export let loading = false;
  export let lastUpdated: Date | null = null;
</script>

<div class="status-bar">
  <div class="status-bar__left">
    <span class="status-dot" class:status-dot--online={online}></span>
    <span class="status-text">{online ? 'API Connected' : 'API Disconnected'}</span>
    {#if lastUpdated}
      <span class="status-sep">/</span>
      <span class="status-text">Updated {lastUpdated.toLocaleTimeString('id-ID')}</span>
    {/if}
  </div>
  <div class="status-bar__right">
    {#if loading}
      <span class="status-loading">Loading</span>
    {/if}
    <span class="kbd-row"><kbd>?</kbd> shortcuts</span>
  </div>
</div>

<style>
  .status-bar {
    min-height: 38px;
    border-bottom: 1px solid var(--surface-border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-4);
    padding: 0 var(--sp-4);
    background: var(--surface-base);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .status-bar__left,
  .status-bar__right {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    min-width: 0;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--signal-bear);
    box-shadow: 0 0 8px var(--signal-bear);
  }

  .status-dot--online {
    background: var(--signal-bull);
    box-shadow: 0 0 8px var(--signal-bull);
  }

  .status-text {
    white-space: nowrap;
  }

  .status-sep {
    color: var(--text-muted);
  }

  .status-loading {
    color: var(--accent-gold);
  }
</style>
