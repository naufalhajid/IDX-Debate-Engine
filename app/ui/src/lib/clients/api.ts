import { get } from 'svelte/store';
import { apiKey } from '$lib/stores/session';
import type { DebateEvent, StockResult } from '$lib/types';

const BASE = 'http://localhost:8000';

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
    throw new Error(err?.detail?.message ?? err?.message ?? 'Request gagal');
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
        fresh_count: number;
        stale_count: number;
      };
    }>('/api/health'),
  validateKey: () => apiFetch<{ valid: boolean }>('/api/validate-key'),
  stocks: () => apiFetch<Array<Record<string, unknown>>>('/api/stocks'),
  stockDetail: (ticker: string) =>
    apiFetch<Record<string, unknown>>(`/api/stocks/${ticker}`),
  results: () => apiFetch<StockResult[]>('/api/results'),

  streamDebate(
    tickers: string[],
    onEvent: (event: DebateEvent) => void,
    onDone: () => void,
    onError?: (message: string) => void
  ) {
    const controller = new AbortController();
    fetch(`${BASE}/api/debate/stream`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ tickers }),
      signal: controller.signal
    })
      .then(async (res) => {
        if (!res.ok) {
          const err = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
          throw new Error(err?.detail?.message ?? err?.message ?? 'Stream gagal');
        }
        if (!res.body) throw new Error('Stream tidak tersedia');
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
            
            const data = cleanFrame.replace(/^data:\s*/m, '').trim();
            if (!data) continue;
            if (data === '[DONE]') {
              onDone();
              return;
            }
            try {
              onEvent(JSON.parse(data) as DebateEvent);
            } catch {
              onError?.('Event stream tidak bisa dibaca');
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
