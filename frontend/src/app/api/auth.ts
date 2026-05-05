import { BASE_URL } from './config';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface User {
  id: number;
  email: string;
  name: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function handleAuthResponse(res: Response): Promise<AuthResponse> {
  if (res.ok) return res.json();
  const body = await res.json().catch(() => ({}));
  throw { status: res.status, message: body?.detail || res.statusText };
}

// ─── Auth API ────────────────────────────────────────────────────────────────

export async function login(email: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
    credentials: 'include',
  });
  return handleAuthResponse(res);
}

export async function signup(
  email: string,
  name: string,
  password: string,
): Promise<{ message: string }> {
  const res = await fetch(`${BASE_URL}/auth/signup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, name, password }),
  });
  if (res.ok) return res.json();
  const body = await res.json().catch(() => ({}));
  throw { status: res.status, message: body?.detail || res.statusText };
}

export async function refreshToken(): Promise<AuthResponse> {
  const res = await fetch(`${BASE_URL}/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
  });
  return handleAuthResponse(res);
}

export async function logout(): Promise<void> {
  await fetch(`${BASE_URL}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  });
}
