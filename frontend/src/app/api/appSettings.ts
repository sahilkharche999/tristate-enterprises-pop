import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export interface AppSettingsPayload {
  global_reserve_inflation_rate: number;
}

export async function getAppSettings(): Promise<AppSettingsPayload> {
  const res = await fetch(`${BASE_URL}/app-settings`, {
    headers: authHeaders(),
  });
  return handleResponse<AppSettingsPayload>(res);
}

export async function updateAppSettings(payload: AppSettingsPayload): Promise<AppSettingsPayload> {
  const res = await fetch(`${BASE_URL}/app-settings`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<AppSettingsPayload>(res);
}
