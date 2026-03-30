// Shared authenticated HTTP helpers used across frontend API modules.

let getAccessToken: (() => string | null) | null = null;

export function setTokenAccessor(fn: () => string | null) {
  getAccessToken = fn;
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

export async function handleResponse<T>(res: Response): Promise<T> {
  if (res.ok) return res.json() as Promise<T>;
  if (res.status === 401) {
    window.location.href = '/';
    throw { status: 401, message: 'Session expired' };
  }
  throw { status: res.status, message: await extractErrorMessage(res) };
}

export async function handleBlobResponse(res: Response): Promise<Blob> {
  if (res.ok) return res.blob();
  throw { status: res.status, message: await extractErrorMessage(res) };
}
