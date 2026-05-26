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
    isStreaming,
    debateStats
  } from '$lib/stores/dashboard';
  import { stockList } from '$lib/stores/metadata';
  import { portfolioEquity } from '$lib/stores/session';
  import { toast } from '$lib/stores/toast';
  import type { DebateEvent, StockResult } from '$lib/types';

  let loading = true;
  let serverOnline = false;
  let lastUpdated: Date | null = null;
  let latestDebateDate: string | null = null;
  let pollInterval: ReturnType<typeof setInterval>;
  let stopStream: (() => void) | null = null;
  let noResultsToastShown = false;
  let pendingStreamTickers = new Set<string>();

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
      if (health.status === 'fulfilled') {
        latestDebateDate = health.value.latest_debate_date;
        const currentStats = get(debateStats);
        if (JSON.stringify(currentStats) !== JSON.stringify(health.value.debate_stats)) {
          debateStats.set(health.value.debate_stats);
        }
      }
      if (stocks.status === 'fulfilled') {
        const nextStocks = stocks.value.map((s: Record<string, unknown>) => ({
          ticker: String(s.ticker ?? ''),
          name: String(s.name ?? ''),
          market_cap: typeof s.market_cap === 'number' ? s.market_cap : null,
          home_page: typeof s.home_page === 'string' ? s.home_page : null
        }));
        const currentStocks = get(stockList);
        if (JSON.stringify(currentStocks) !== JSON.stringify(nextStocks)) {
          stockList.set(nextStocks);
        }
      }
      if (results.status === 'fulfilled') {
        noResultsToastShown = false;
        const currentResults = get(allResults);
        if (JSON.stringify(currentResults) !== JSON.stringify(results.value)) {
          allResults.set(results.value);
        }
        if (!get(activeTicker) && results.value[0]) activeTicker.set(results.value[0].ticker);
        lastUpdated = new Date();
      } else {
        if (!noResultsToastShown) {
          toast('warning', results.reason?.message ?? 'No analysis results found');
          noResultsToastShown = true;
        }
      }
    } catch (error: unknown) {
      serverOnline = false;
      toast('error', (error as Error).message ?? 'Failed to load data');
    } finally {
      loading = false;
    }
  }

  function handleStreamEvent(event: DebateEvent) {
    debateStream.update((items) => {
      if (event.type === 'verdict') {
        const stage = event.stage ?? 'interim';
        const idx = items.findIndex(
          (item) =>
            item.type === 'verdict' &&
            item.ticker === event.ticker &&
            (item.stage ?? 'interim') === stage
        );
        if (idx !== -1) {
          const updated = [...items];
          updated[idx] = event;
          return updated;
        }
      }
      return [...items, event];
    });
    activeTicker.set(event.ticker);
    if (event.type === 'verdict') upsertResult(event.result);
    if (event.type === 'done' || event.type === 'error') {
      pendingStreamTickers.delete(event.ticker);
    }
    if (event.type === 'error') {
      toast('error', `${event.ticker}: ${event.message}`);
    }
  }

  function startDebate(tickers: string[]) {
    const cleanedTickers = [...new Set(tickers.map((ticker) => ticker.trim().toUpperCase()).filter(Boolean))];
    if (!cleanedTickers.length || get(isStreaming)) return;
    stopStream?.();
    debateStream.set([]);
    pendingStreamTickers = new Set(cleanedTickers);
    isStreaming.set(true);
    stopStream = api.streamDebate(
      cleanedTickers,
      {
        total_capital: get(portfolioEquity),
        max_loss_pct: 0.02,
        max_positions: 5
      },
      handleStreamEvent,
      () => {
        isStreaming.set(false);
        pendingStreamTickers = new Set();
        stopStream = null;
        toast('success', 'Debate stream completed');
        loadData();
      },
      (message) => {
        isStreaming.set(false);
        pendingStreamTickers = new Set();
        stopStream = null;
        toast('error', message);
        loadData();
      }
    );
  }
  let rightWidth = 450;
  let isResizing = false;
  let workspaceEl: HTMLDivElement | undefined = undefined;

  function startResizing(event: MouseEvent) {
    event.preventDefault();
    isResizing = true;
    const startX = event.clientX;
    const startWidth = rightWidth;

    function onMouseMove(moveEvent: MouseEvent) {
      if (!isResizing) return;
      const deltaX = moveEvent.clientX - startX;
      const newWidth = startWidth - deltaX;

      if (workspaceEl) {
        const totalWidth = workspaceEl.clientWidth;
        const minLeftWidth = 500;
        const minRightWidth = 350;
        const maxRightWidth = totalWidth - minLeftWidth - 12; // 12px for splitter
        rightWidth = Math.max(minRightWidth, Math.min(maxRightWidth, newWidth));
      } else {
        rightWidth = Math.max(350, Math.min(800, newWidth));
      }
    }

    function onMouseUp() {
      isResizing = false;
      localStorage.setItem('idx-dashboard-right-width', String(rightWidth));
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    }

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  }

  function startResizingTouch(event: TouchEvent) {
    if (event.touches.length !== 1) return;
    isResizing = true;
    const startX = event.touches[0].clientX;
    const startWidth = rightWidth;

    function onTouchMove(moveEvent: TouchEvent) {
      if (!isResizing || moveEvent.touches.length !== 1) return;
      const deltaX = moveEvent.touches[0].clientX - startX;
      const newWidth = startWidth - deltaX;

      if (workspaceEl) {
        const totalWidth = workspaceEl.clientWidth;
        const minLeftWidth = 500;
        const minRightWidth = 350;
        const maxRightWidth = totalWidth - minLeftWidth - 12;
        rightWidth = Math.max(minRightWidth, Math.min(maxRightWidth, newWidth));
      } else {
        rightWidth = Math.max(350, Math.min(800, newWidth));
      }
    }

    function onTouchEnd() {
      isResizing = false;
      localStorage.setItem('idx-dashboard-right-width', String(rightWidth));
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
    }

    window.addEventListener('touchmove', onTouchMove, { passive: true });
    window.addEventListener('touchend', onTouchEnd);
  }

  let sidebarWidth = 260;
  let isSidebarResizing = false;
  let isSidebarCollapsed = false;

  function startSidebarResizing(event: MouseEvent) {
    event.preventDefault();
    isSidebarResizing = true;
    const startX = event.clientX;
    const startWidth = sidebarWidth;

    function onMouseMove(moveEvent: MouseEvent) {
      if (!isSidebarResizing) return;
      const deltaX = moveEvent.clientX - startX;
      const newWidth = startWidth + deltaX;
      sidebarWidth = Math.max(220, Math.min(380, newWidth));
    }

    function onMouseUp() {
      isSidebarResizing = false;
      localStorage.setItem('idx-dashboard-sidebar-width', String(sidebarWidth));
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    }

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  }

  function startSidebarResizingTouch(event: TouchEvent) {
    if (event.touches.length !== 1) return;
    isSidebarResizing = true;
    const startX = event.touches[0].clientX;
    const startWidth = sidebarWidth;

    function onTouchMove(moveEvent: TouchEvent) {
      if (!isSidebarResizing || moveEvent.touches.length !== 1) return;
      const deltaX = moveEvent.touches[0].clientX - startX;
      const newWidth = startWidth + deltaX;
      sidebarWidth = Math.max(220, Math.min(380, newWidth));
    }

    function onTouchEnd() {
      isSidebarResizing = false;
      localStorage.setItem('idx-dashboard-sidebar-width', String(sidebarWidth));
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
    }

    window.addEventListener('touchmove', onTouchMove, { passive: true });
    window.addEventListener('touchend', onTouchEnd);
  }

  $: if (isSidebarCollapsed) {
    sidebarWidth = 72;
  } else if (typeof window !== 'undefined') {
    const saved = localStorage.getItem('idx-dashboard-sidebar-width');
    if (saved) {
      const parsed = parseInt(saved, 10);
      if (!isNaN(parsed) && parsed >= 220 && parsed <= 380) {
        sidebarWidth = parsed;
      }
    } else {
      sidebarWidth = 260;
    }
  }

  $: if (typeof window !== 'undefined') {
    localStorage.setItem('idx-dashboard-sidebar-collapsed', String(isSidebarCollapsed));
  }

  onMount(async () => {
    const savedRight = localStorage.getItem('idx-dashboard-right-width');
    if (savedRight) {
      const parsed = parseInt(savedRight, 10);
      if (!isNaN(parsed) && parsed >= 300 && parsed <= 1000) {
        rightWidth = parsed;
      }
    }
    const savedSidebar = localStorage.getItem('idx-dashboard-sidebar-width');
    if (savedSidebar) {
      const parsed = parseInt(savedSidebar, 10);
      if (!isNaN(parsed) && parsed >= 220 && parsed <= 380) {
        sidebarWidth = parsed;
      }
    }
    const savedCollapsed = localStorage.getItem('idx-dashboard-sidebar-collapsed');
    if (savedCollapsed) {
      isSidebarCollapsed = savedCollapsed === 'true';
    }
    await loadData();
    pollInterval = setInterval(loadData, 5_000);
  });

  onDestroy(() => {
    clearInterval(pollInterval);
    stopStream?.();
  });
</script>

<div class="page-viewport">
  <div class="app-container">
    <Sidebar bind:collapsed={isSidebarCollapsed} width={sidebarWidth} />

    {#if !isSidebarCollapsed}
      <button
        type="button"
        class="sidebar-splitter"
        class:sidebar-splitter--resizing={isSidebarResizing}
        onmousedown={startSidebarResizing}
        ontouchstart={startSidebarResizingTouch}
        aria-label="Resize sidebar"
      >
        <div class="splitter-handle"></div>
      </button>
    {:else}
      <div class="sidebar-collapsed-border"></div>
    {/if}

    <main class="main-content">
      <ServerStatusBar online={serverOnline} {loading} {lastUpdated} {latestDebateDate} />

      <div 
        class="workspace-grid" 
        bind:this={workspaceEl} 
        style="grid-template-columns: 1fr auto {rightWidth}px; gap: 0;"
      >
        <section class="candidates-area">
          {#if loading}
            <SkeletonLoader rows={10} />
          {:else}
            <CandidatesTable onDebate={startDebate} />
          {/if}
        </section>

        <button
          type="button"
          class="workspace-splitter"
          class:workspace-splitter--resizing={isResizing}
          onmousedown={startResizing}
          ontouchstart={startResizingTouch}
          aria-label="Resize panels"
        >
          <div class="splitter-handle"></div>
        </button>

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
    grid-template-columns: minmax(500px, 1fr) auto 450px;
    gap: 0;
    padding: var(--sp-4);
  }

  .candidates-area,
  .debate-area {
    min-width: 0;
    min-height: 0;
  }

  .workspace-splitter {
    width: 16px;
    padding: 0;
    border: 0;
    cursor: col-resize;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    user-select: none;
    background: transparent;
    color: inherit;
    transition: background-color 0.2s;
  }

  .splitter-handle {
    width: 2px;
    height: 40px;
    background-color: var(--surface-border-strong);
    border-radius: 1px;
    transition: background-color 0.2s, height 0.2s, box-shadow 0.2s;
    position: relative;
  }

  .splitter-handle::before,
  .splitter-handle::after {
    content: '';
    position: absolute;
    left: -4px;
    width: 2px;
    height: 2px;
    border-radius: 50%;
    background-color: var(--surface-border-strong);
    top: calc(50% - 4px);
    transition: background-color 0.2s;
  }
  .splitter-handle::after {
    top: calc(50% + 2px);
  }

  .workspace-splitter:hover {
    background-color: rgba(255, 90, 0, 0.04);
  }

  .workspace-splitter:hover .splitter-handle,
  .workspace-splitter--resizing .splitter-handle {
    background-color: var(--accent-cyan);
    height: 80px;
    box-shadow: 0 0 8px var(--accent-cyan);
  }

  .workspace-splitter:hover .splitter-handle::before,
  .workspace-splitter:hover .splitter-handle::after,
  .workspace-splitter--resizing .splitter-handle::before,
  .workspace-splitter--resizing .splitter-handle::after {
    background-color: var(--accent-cyan);
  }

  .sidebar-splitter {
    width: 12px;
    padding: 0;
    cursor: col-resize;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    user-select: none;
    background: transparent;
    color: inherit;
    border-right: 1px solid var(--surface-border);
    transition: background-color 0.2s, border-color 0.2s;
  }

  .sidebar-splitter:hover {
    background-color: rgba(255, 90, 0, 0.04);
    border-color: transparent;
  }

  .sidebar-splitter .splitter-handle {
    width: 2px;
    height: 40px;
    background-color: var(--surface-border-strong);
    border-radius: 1px;
    transition: background-color 0.2s, height 0.2s, box-shadow 0.2s;
    position: relative;
  }

  .sidebar-splitter .splitter-handle::before,
  .sidebar-splitter .splitter-handle::after {
    content: '';
    position: absolute;
    left: -4px;
    width: 2px;
    height: 2px;
    border-radius: 50%;
    background-color: var(--surface-border-strong);
    top: calc(50% - 4px);
    transition: background-color 0.2s;
  }
  .sidebar-splitter .splitter-handle::after {
    top: calc(50% + 2px);
  }

  .sidebar-splitter:hover .splitter-handle,
  .sidebar-splitter--resizing .splitter-handle {
    background-color: var(--accent-cyan);
    height: 80px;
    box-shadow: 0 0 8px var(--accent-cyan);
  }

  .sidebar-splitter:hover .splitter-handle::before,
  .sidebar-splitter:hover .splitter-handle::after,
  .sidebar-splitter--resizing .splitter-handle::before,
  .sidebar-splitter--resizing .splitter-handle::after {
    background-color: var(--accent-cyan);
  }

  .sidebar-collapsed-border {
    width: 1px;
    background-color: var(--surface-border);
    height: 100%;
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
      grid-template-columns: 1fr !important;
      gap: var(--sp-4) !important;
    }

    .workspace-splitter,
    .sidebar-splitter,
    .sidebar-collapsed-border {
      display: none !important;
    }

    .debate-area {
      min-height: 600px;
    }
  }
</style>
