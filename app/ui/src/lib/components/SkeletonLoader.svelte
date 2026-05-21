<script lang="ts">
  export let rows = 8;
  export let type: 'table' | 'card' = 'table';
</script>

{#if type === 'table'}
  <div class="skeleton-table">
    {#each Array(rows) as _}
      <div class="skel-row">
        <div class="skel-cell skel-cell--sm"></div>
        <div class="skel-cell skel-cell--md"></div>
        <div class="skel-cell skel-cell--lg"></div>
        <div class="skel-cell skel-cell--sm"></div>
        <div class="skel-cell skel-cell--md"></div>
      </div>
    {/each}
  </div>
{:else}
  <div class="skel-card"></div>
{/if}

<style>
  .skeleton-table {
    border-top: 1px solid var(--surface-border);
  }

  .skel-row {
    display: flex;
    gap: var(--sp-4);
    padding: var(--sp-3) var(--sp-4);
    border-bottom: 1px solid var(--surface-border);
  }

  .skel-cell,
  .skel-card {
    background: linear-gradient(
      90deg,
      var(--surface-2) 25%,
      var(--surface-3) 50%,
      var(--surface-2) 75%
    );
    background-size: 200% 100%;
    animation: shimmer 1.5s infinite linear;
  }

  .skel-cell {
    height: 14px;
    border-radius: var(--radius-sm);
  }

  .skel-card {
    min-height: 220px;
    border-radius: var(--radius-md);
  }

  .skel-cell--sm {
    width: 64px;
  }

  .skel-cell--md {
    width: 128px;
  }

  .skel-cell--lg {
    flex: 1;
  }

  @keyframes shimmer {
    0% {
      background-position: -200% 0;
    }

    100% {
      background-position: 200% 0;
    }
  }
</style>
