<script lang="ts">
  import '@fontsource/dm-sans/400.css';
  import '@fontsource/dm-sans/600.css';
  import '@fontsource/jetbrains-mono/400.css';
  import '@fontsource/jetbrains-mono/600.css';
  import '../app.css';
  import ToastStack from '$lib/components/ToastStack.svelte';
  import { browser } from '$app/environment';
  import { onMount } from 'svelte';
  import { searchQuery } from '$lib/stores/dashboard';

  onMount(() => {
    if (!browser) return;
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA'].includes(target.tagName)) return;
      if (event.key.toLowerCase() === 'k') {
        event.preventDefault();
        document.querySelector<HTMLInputElement>('.search-input')?.focus();
      }
      if (event.key.toLowerCase() === 'e') {
        window.dispatchEvent(new CustomEvent('idx-export-csv'));
      }
      if (event.key === 'Escape') {
        searchQuery.set('');
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });
</script>

<div class="app-shell">
  <slot />
  <ToastStack />
</div>
