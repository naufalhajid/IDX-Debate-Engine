<script lang="ts">
  import { toasts } from '$lib/stores/toast';

  function icon(type: string) {
    if (type === 'success') return 'OK';
    if (type === 'error') return 'ERR';
    if (type === 'warning') return 'WARN';
    return 'INFO';
  }
</script>

<div class="toast-stack" aria-live="polite">
  {#each $toasts as item (item.id)}
    <div class="toast toast--{item.type}" role="alert">
      <span class="toast__icon">{icon(item.type)}</span>
      <span class="toast__msg">{item.message}</span>
    </div>
  {/each}
</div>

<style>
  .toast-stack {
    position: fixed;
    right: var(--sp-6);
    bottom: var(--sp-6);
    z-index: var(--z-toast);
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
    pointer-events: none;
  }

  .toast {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    max-width: min(420px, calc(100vw - 48px));
    padding: var(--sp-3) var(--sp-4);
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-md);
    background: var(--surface-3);
    color: var(--text-primary);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    animation: slide-in 0.18s ease-out;
  }

  .toast--success {
    border-color: var(--signal-bull);
  }

  .toast--error {
    border-color: var(--signal-bear);
  }

  .toast--warning {
    border-color: var(--signal-hold);
  }

  .toast--info {
    border-color: var(--accent-cyan);
  }

  .toast__icon {
    min-width: 34px;
    color: var(--text-code);
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .toast__msg {
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.4;
  }

  @keyframes slide-in {
    from {
      transform: translateX(120%);
      opacity: 0;
    }

    to {
      transform: translateX(0);
      opacity: 1;
    }
  }
</style>
