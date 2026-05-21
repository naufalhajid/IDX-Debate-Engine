import { writable } from 'svelte/store';

export type Toast = {
  id: string;
  type: 'success' | 'error' | 'info' | 'warning';
  message: string;
};

export const toasts = writable<Toast[]>([]);

export function toast(type: Toast['type'], message: string, duration = 4000) {
  const id = Math.random().toString(36).slice(2);
  toasts.update((items) => [...items, { id, type, message }]);
  setTimeout(() => {
    toasts.update((items) => items.filter((item) => item.id !== id));
  }, duration);
}
