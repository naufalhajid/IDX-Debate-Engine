<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { api } from '$lib/clients/api';
  import CandidatesTable from '$lib/components/CandidatesTable.svelte';
  import DebateTimeline from '$lib/components/DebateTimeline.svelte';
  import ServerStatusBar from '$lib/components/ServerStatusBar.svelte';
  import Sidebar from '$lib/components/Sidebar.svelte';
  import SkeletonLoader from '$lib/components/SkeletonLoader.svelte';
  import {
    activeTicker,
    allResults,
    debateStream,
    isStreaming
  } from '$lib/stores/dashboard';
  import { stockList } from '$lib/stores/metadata';
  import { toast } from '$lib/stores/toast';
  import type { DebateEvent, StockResult } from '$lib/types';

  let loading = true;
  let serverOnline = false;
  let lastUpdated: Date | null = null;
  let pollInterval: ReturnType<typeof setInterval>;
  let stopStream: (() => void) | null = null;

  function upsertResult(result: StockResult) {
    allResults.update((items) => {
      const index = items.findIndex((item) => item.ticker === result.ticker);
      if (index === -1) return [result, ...items];
      const next = [...items];
      next[index] = result;
      return next;
    });
  }

  async function loadData() {
    try {
      const [health, results, stocks] = await Promise.allSettled([
        api.health(),
        api.results(),
        api.stocks()
      ]);
      serverOnline = health.status === 'fulfilled';
      if (stocks.status === 'fulfilled') {
        stockList.set(
          stocks.value.map((s: Record<string, unknown>) => ({
            ticker: String(s.ticker ?? ''),
            name: String(s.name ?? ''),
            market_cap: typeof s.market_cap === 'number' ? s.market_cap : null,
            home_page: typeof s.home_page === 'string' ? s.home_page : null
          }))
        );
      }
      if (results.status === 'fulfilled') {
        allResults.set(results.value);
        if (!get(activeTicker) && results.value[0]) activeTicker.set(results.value[0].ticker);
        lastUpdated = new Date();
      } else {
        toast('warning', results.reason?.message ?? 'Belum ada hasil analisis');
      }
    } catch (error: unknown) {
      serverOnline = false;
      toast('error', (error as Error).message ?? 'Gagal memuat data');
    } finally {
      loading = false;
    }
  }

  function handleStreamEvent(event: DebateEvent) {
    debateStream.update((items) => [...items, event]);
    activeTicker.set(event.ticker);
    if (event.type === 'verdict') upsertResult(event.result);
    if (event.type === 'done' || event.type === 'error') {
      isStreaming.set(false);
    }
    if (event.type === 'error') {
      toast('error', `${event.ticker}: ${event.message}`);
    }
  }

  function startDebate(tickers: string[]) {
    if (!tickers.length || get(isStreaming)) return;
    stopStream?.();
    debateStream.set([]);
    isStreaming.set(true);
    stopStream = api.streamDebate(
      tickers,
      handleStreamEvent,
      () => {
        isStreaming.set(false);
        stopStream = null;
        toast('success', 'Debate stream selesai');
      },
      (message) => {
        isStreaming.set(false);
        stopStream = null;
        toast('error', message);
      }
    );
  }

  onMount(async () => {
    await loadData();
    pollInterval = setInterval(loadData, 60_000);
  });

  onDestroy(() => {
    clearInterval(pollInterval);
    stopStream?.();
  });
</script>

<div class="page-viewport">
  <div class="app-container">
    <Sidebar />

    <main class="main-content">
      <ServerStatusBar online={serverOnline} {loading} {lastUpdated} />

      <div class="workspace-grid">
        <section class="candidates-area">
          {#if loading}
            <SkeletonLoader rows={10} />
          {:else}
            <CandidatesTable onDebate={startDebate} />
          {/if}
        </section>

        <section class="debate-area">
          <DebateTimeline />
        </section>
      </div>
    </main>
  </div>
</div>

<style>
  .page-viewport {
    width: 100vw;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: clamp(16px, 3vw, 40px);
    background: var(--surface-base);
  }

  .app-container {
    width: 100%;
    max-width: 1600px;
    height: 100%;
    min-height: 700px;
    display: flex;
    overflow: hidden;
    border: 1px solid var(--surface-border);
    border-radius: var(--radius-shell);
    background: var(--surface-frame);
    box-shadow: var(--shadow-panel);
  }

  .main-content {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    background: transparent;
  }

  .workspace-grid {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(500px, 1fr) 450px;
    gap: var(--sp-4);
    padding: var(--sp-4);
  }

  .candidates-area,
  .debate-area {
    min-width: 0;
    min-height: 0;
  }

  @media (max-width: 1080px) {
    :global(body) {
      overflow: auto;
    }

    .page-viewport {
      height: auto;
      min-height: 100vh;
      align-items: flex-start;
      padding: var(--sp-2);
    }

    .app-container {
      height: auto;
      min-height: 0;
      flex-direction: column;
    }

    .workspace-grid {
      grid-template-columns: 1fr;
    }

    .debate-area {
      min-height: 600px;
    }
  }
</style>
