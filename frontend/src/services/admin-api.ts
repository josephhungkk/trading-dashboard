const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? '';

export type ConfigValueType = 'str' | 'int' | 'bool' | 'json';
type JsonValue = string | number | boolean | null | { [key: string]: JsonValue } | JsonValue[];
export type ConfigValue = JsonValue;

export interface ConfigRecord {
  namespace: string;
  key: string;
  value: ConfigValue;
  value_type: ConfigValueType;
  created_at: string;
  updated_at: string;
}

export interface SecretMetadata {
  namespace: string;
  key: string;
  value_type: ConfigValueType;
  created_at: string;
  updated_at: string;
}

export interface SecretReveal {
  namespace: string;
  key: string;
  value: ConfigValue;
  value_type: ConfigValueType;
}

interface ConfigPayload {
  namespace: string;
  key: string;
  value: ConfigValue;
  value_type: ConfigValueType;
}

function pathPart(value: string): string {
  return encodeURIComponent(value);
}

async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set('Content-Type', 'application/json');
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers,
  });
  if (!r.ok) throw new Error(await responseMessage(r));
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

async function responseMessage(r: Response): Promise<string> {
  try {
    const body = (await r.json()) as unknown;
    if (isDetailBody(body)) return `admin ${r.status}: ${body.detail}`;
  } catch {
    return `admin ${r.status}`;
  }
  return `admin ${r.status}`;
}

function isDetailBody(body: unknown): body is { detail: string } {
  return (
    typeof body === 'object'
    && body !== null
    && 'detail' in body
    && typeof body.detail === 'string'
  );
}

export function listConfigs(): Promise<ConfigRecord[]> {
  return adminFetch<ConfigRecord[]>('/api/admin/config');
}

export function upsertConfig(payload: ConfigPayload): Promise<ConfigRecord> {
  return adminFetch<ConfigRecord>(
    `/api/admin/config/${pathPart(payload.namespace)}/${pathPart(payload.key)}`,
    { method: 'PUT', body: JSON.stringify(payload) },
  );
}

export function deleteConfig(namespace: string, key: string): Promise<void> {
  return adminFetch<undefined>(`/api/admin/config/${pathPart(namespace)}/${pathPart(key)}`, {
    method: 'DELETE',
  });
}

export function listSecrets(): Promise<SecretMetadata[]> {
  return adminFetch<SecretMetadata[]>('/api/admin/secrets');
}

export function upsertSecret(payload: ConfigPayload): Promise<SecretMetadata> {
  return adminFetch<SecretMetadata>(
    `/api/admin/secrets/${pathPart(payload.namespace)}/${pathPart(payload.key)}`,
    { method: 'PUT', body: JSON.stringify(payload) },
  );
}

export function deleteSecret(namespace: string, key: string): Promise<void> {
  return adminFetch<undefined>(`/api/admin/secrets/${pathPart(namespace)}/${pathPart(key)}`, {
    method: 'DELETE',
  });
}

export function revealSecret(namespace: string, key: string): Promise<SecretReveal> {
  return adminFetch<SecretReveal>(
    `/api/admin/secrets/${pathPart(namespace)}/${pathPart(key)}/reveal`,
    { method: 'POST' },
  );
}
