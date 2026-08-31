import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export interface SectionCatalogItem {
  template: string;
  label: string;
  required: boolean;
  hidden: boolean;
}

export interface AppSettingsPayload {
  global_reserve_inflation_rate?: number;
  disclosure_section_order?: string[] | null;
  disclosure_hidden_sections?: string[] | null;
  section_catalog?: SectionCatalogItem[];
  has_firm_signature?: boolean;
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

export function firmSignatureUrl(): string {
  return `${BASE_URL}/app-settings/signature`;
}

export async function uploadFirmSignature(file: File): Promise<AppSettingsPayload> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE_URL}/app-settings/signature`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  return handleResponse<AppSettingsPayload>(res);
}

export async function deleteFirmSignature(): Promise<AppSettingsPayload> {
  const res = await fetch(`${BASE_URL}/app-settings/signature`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return handleResponse<AppSettingsPayload>(res);
}
