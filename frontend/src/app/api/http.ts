// Shared authenticated HTTP helpers used across frontend API modules.

import { BASE_URL } from './config';

let getAccessToken: (() => string | null) | null = null;
let setAccessTokenFn: ((token: string) => void) | null = null;
let isRefreshing = false;
let refreshQueue: Array<{ resolve: (token: string) => void; reject: (err: unknown) => void }> = [];

export function setTokenAccessor(fn: () => string | null) {
  getAccessToken = fn;
}

export function setTokenUpdater(fn: (token: string) => void) {
  setAccessTokenFn = fn;
}

export function authHeaders(): HeadersInit {
  const token = getAccessToken?.();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function extractErrorMessage(res: Response): Promise<string> {
  let message = res.statusText;
  try {
    const body = await res.json();
    if (body?.detail) {
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    }
  } catch {
    // Ignore response parsing failures and fall back to the HTTP status text.
  }
  return message;
}

async function tryRefreshToken(): Promise<string | null> {
  if (isRefreshing) {
    return new Promise((resolve, reject) => {
      refreshQueue.push({ resolve, reject });
    });
  }

  isRefreshing = true;
  try {
    const res = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    });
    if (!res.ok) return null;
    const data = await res.json();
    const newToken = data.access_token as string;
    setAccessTokenFn?.(newToken);
    refreshQueue.forEach((q) => q.resolve(newToken));
    return newToken;
  } catch {
    refreshQueue.forEach((q) => q.reject(new Error('Refresh failed')));
    return null;
  } finally {
    isRefreshing = false;
    refreshQueue = [];
  }
}

export async function handleResponse<T>(res: Response): Promise<T> {
  if (res.ok) return res.json() as Promise<T>;
  if (res.status === 401) {
    // Try to refresh before giving up
    const newToken = await tryRefreshToken();
    if (newToken && res.url) {
      // Retry the original request with the new token
      const retryRes = await fetch(res.url, {
        headers: { Authorization: `Bearer ${newToken}` },
        credentials: 'include',
      });
      if (retryRes.ok) return retryRes.json() as Promise<T>;
    }
    // Refresh failed — redirect to login
    window.location.href = '/';
    throw { status: 401, message: 'Session expired' };
  }
  throw { status: res.status, message: await extractErrorMessage(res) };
}

export async function handleBlobResponse(res: Response): Promise<Blob> {
  if (res.ok) return res.blob();
  if (res.status === 401) {
    const newToken = await tryRefreshToken();
    if (newToken && res.url) {
      const retryRes = await fetch(res.url, {
        headers: { Authorization: `Bearer ${newToken}` },
        credentials: 'include',
      });
      if (retryRes.ok) return retryRes.blob();
    }
    window.location.href = '/';
  }
  throw { status: res.status, message: await extractErrorMessage(res) };
}
