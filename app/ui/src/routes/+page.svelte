<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { api } from '$lib/clients/api';
  import CandidatesTable from '$lib/components/CandidatesTable.svelte';
  import DebateTimeline from '$lib/components/DebateTimeline.svelte';
  import ServerStatusBar from '$lib/components/ServerStatusBar.svelte';
  import Sidebar from '$lib/components/Sidebar.svelte';
  import SkeletonLoader from '$lib/components/SkeletonLoader.svelte';
  import TradeBox from '$lib/components/TradeBox.svelte';
  import {
    activeTicker,
    allResults,
    debateStream,
    isStreaming
  } from '$lib/stores/dashboard';
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
      const [health, results] = await Promise.allSettled([api.health(), api.results()]);
      serverOnline = health.status === 'fulfilled';
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
    if ('ticker' in event) activeTicker.set(event.ticker);
    if (event.type === 'verdict') upsertResult(event.result);
    if (event.type === 'error') toast('error', `${event.ticker}: ${event.message}`);
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

<div class="page">
  <Sidebar />

  <main class="main">
    <ServerStatusBar online={serverOnline} {loading} {lastUpdated} />

    <div class="content-grid">
      <section class="col-table">
        {#if loading}
          <SkeletonLoader rows={12} />
        {:else}
          <CandidatesTable onDebate={startDebate} />
        {/if}
      </section>

      <div class="col-detail">
        <section class="detail-top">
          <TradeBox />
        </section>
        <section class="detail-bottom">
          <DebateTimeline />
        </section>
      </div>
    </div>
  </main>
</div>

<style>
  .page {
    display: flex;
    min-height: 100vh;
    background: var(--surface-base);
    color: var(--text-primary);
  }

  .main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  .content-grid {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 400px;
  }

  .col-table {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--surface-border);
    overflow: hidden;
  }

  .col-detail {
    min-height: 0;
    display: flex;
    flex-direction: column;
    background: var(--surface-base);
  }

  .detail-top {
    border-bottom: 1px solid var(--surface-border);
    padding: var(--sp-4);
  }

  .detail-bottom {
    flex: 1;
    min-height: 0;
    padding: var(--sp-4);
    overflow: hidden;
  }

  @media (max-width: 1120px) {
    .page {
      display: block;
      overflow: auto;
    }

    .content-grid {
      grid-template-columns: 1fr;
    }

    .col-detail {
      min-height: 720px;
    }
  }
</style>
