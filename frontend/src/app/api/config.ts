const viteApiUrl =
  typeof import.meta !== 'undefined' && import.meta.env
    ? (import.meta.env.VITE_API_URL as string | undefined)
    : undefined;

export const BASE_URL = viteApiUrl || 'http://localhost:8000';
