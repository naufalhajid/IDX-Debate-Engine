import { get } from 'svelte/store';
import { apiKey } from '$lib/stores/session';
import type { DebateEvent, StockResult } from '$lib/types';

const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export interface DebateRunConfig {
  total_capital?: number;
  max_loss_pct?: number;
  max_positions?: number;
}

function headers(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Gemini-API-Key': get(apiKey)
  };
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...headers(), ...init?.headers }
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
    throw new Error(err?.detail?.message ?? err?.message ?? 'Request failed');
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () =>
    apiFetch<{
      status: string;
      results_exist: boolean;
      latest_debate_date: string | null;
      debate_stats: {
        total_debates: number;
        avg_conviction: number;
        avg_confidence: number;
        consensus_rate: number;
        ratings_distribution: Record<string, number>;
        execution_status_distribution: Record<string, number>;
        fresh_count: number;
        stale_count: number;
        corrupt_artifacts: number;
        latest_debate_date: string | null;
      };
    }>('/api/health'),
  validateKey: () => apiFetch<{ valid: boolean }>('/api/validate-key'),
  stocks: () => apiFetch<Array<Record<string, unknown>>>('/api/stocks'),
  stockDetail: (ticker: string) =>
    apiFetch<Record<string, unknown>>(`/api/stocks/${ticker}`),
  results: () => apiFetch<StockResult[]>('/api/results'),

  streamDebate(
    tickers: string[],
    config: DebateRunConfig,
    onEvent: (event: DebateEvent) => void,
    onDone: () => void,
    onError?: (message: string) => void
  ) {
    const controller = new AbortController();
    fetch(`${BASE}/api/debate/stream`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ tickers, ...config }),
      signal: controller.signal
    })
      .then(async (res) => {
        if (!res.ok) {
          const err = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
          throw new Error(err?.detail?.message ?? err?.message ?? 'Stream failed');
        }
        if (!res.body) throw new Error('Stream not available');
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';
          for (const frame of frames) {
            const cleanFrame = frame.trim();
            if (!cleanFrame || cleanFrame.startsWith(':')) continue;
            
            const data = cleanFrame
              .split('\n')
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.replace(/^data:\s*/, ''))
              .join('\n')
              .trim();
            if (!data) continue;
            if (data === '[DONE]') {
              onDone();
              return;
            }
            try {
              onEvent(JSON.parse(data) as DebateEvent);
            } catch {
              onError?.('Unable to parse stream event');
            }
          }
        }
        onDone();
      })
      .catch((error: unknown) => {
        if ((error as Error).name !== 'AbortError') {
          onError?.((error as Error).message);
        }
      });
    return () => controller.abort();
  }
};
