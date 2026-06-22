const viteApiUrl =
  typeof import.meta !== 'undefined' && import.meta.env
    ? (import.meta.env.VITE_API_URL as string | undefined)
    : undefined;

// Use ?? not || so that an empty string (Docker local: same-origin nginx proxy)
// is a valid value and does not fall through to the dev-only default.
export const BASE_URL = viteApiUrl ?? 'http://localhost:8000';
