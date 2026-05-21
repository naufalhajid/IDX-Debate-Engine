import { browser } from '$app/environment';
import { writable } from 'svelte/store';

function createApiKeyStore() {
  const initial = browser ? (localStorage.getItem('gemini_api_key') ?? '') : '';
  const { subscribe, set } = writable<string>(initial);
  return {
    subscribe,
    set: (key: string) => {
      if (browser) localStorage.setItem('gemini_api_key', key);
      set(key);
    },
    clear: () => {
      if (browser) localStorage.removeItem('gemini_api_key');
      set('');
    }
  };
}

export const apiKey = createApiKeyStore();
export const portfolioEquity = writable<number>(100_000_000);
