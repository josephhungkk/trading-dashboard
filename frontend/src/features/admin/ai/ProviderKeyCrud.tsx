import * as React from 'react';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? '';
const NAMESPACE = 'ai_provider';

interface SecretMetadata {
  namespace: string;
  key: string;
  value_type?: string;
  created_at?: string;
  updated_at?: string;
}

function pathPart(value: string): string {
  return encodeURIComponent(value);
}

async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers,
  });
  if (!response.ok) throw new Error(await responseMessage(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as unknown;
    if (isDetailBody(body)) return `admin ${response.status}: ${body.detail}`;
  } catch {
    return `admin ${response.status}`;
  }
  return `admin ${response.status}`;
}

function isDetailBody(body: unknown): body is { detail: string } {
  return (
    typeof body === 'object'
    && body !== null
    && 'detail' in body
    && typeof body.detail === 'string'
  );
}

async function mintCsrfNonce(): Promise<string> {
  const result = await adminFetch<{ nonce: string }>('/api/admin/csrf/issue', {
    method: 'POST',
  });
  return result.nonce;
}

function listProviderSecrets(): Promise<SecretMetadata[]> {
  return adminFetch<SecretMetadata[]>('/api/admin/secrets?namespace=ai_provider');
}

function createProviderSecret(key: string, value: string, nonce: string): Promise<SecretMetadata> {
  return adminFetch<SecretMetadata>('/api/admin/secrets', {
    method: 'POST',
    headers: { 'X-CSRF-Nonce': nonce },
    body: JSON.stringify({ namespace: NAMESPACE, key, value }),
  });
}

function deleteProviderSecret(key: string, nonce: string): Promise<void> {
  return adminFetch<undefined>(`/api/admin/secrets/${pathPart(NAMESPACE)}/${pathPart(key)}`, {
    method: 'DELETE',
    headers: { 'X-CSRF-Nonce': nonce },
  });
}

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : 'Admin request failed';
}

export function ProviderKeyCrud(): React.JSX.Element {
  const [rows, setRows] = React.useState<SecretMetadata[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [deletingKey, setDeletingKey] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [keyName, setKeyName] = React.useState('');
  const [value, setValue] = React.useState('');

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listProviderSecrets());
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- load is async; setState is called in .then/.catch, not synchronously
  React.useEffect(() => { void load(); }, [load]);

  async function addSecret(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmedKey = keyName.trim();
    if (trimmedKey === '' || value === '') return;
    setSaving(true);
    setError(null);
    try {
      const nonce = await mintCsrfNonce();
      await createProviderSecret(trimmedKey, value, nonce);
      setKeyName('');
      setValue('');
      await load();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setSaving(false);
    }
  }

  async function removeSecret(key: string): Promise<void> {
    setDeletingKey(key);
    setError(null);
    try {
      const nonce = await mintCsrfNonce();
      await deleteProviderSecret(key, nonce);
      await load();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setDeletingKey(null);
    }
  }

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-3">
      <p className="text-sm text-fg-muted">
        {loading ? 'Loading provider keys' : `${rows.length} provider key(s)`}
      </p>

      {error && <div className="rounded-md border border-negative bg-negative/10 p-3 text-sm text-negative">{error}</div>}

      <form className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]" onSubmit={(event) => void addSecret(event)}>
        <label htmlFor="provider-key-name" className="grid gap-1 text-sm text-fg">
          Key name
          <Input
            id="provider-key-name"
            value={keyName}
            onChange={(event) => setKeyName(event.currentTarget.value)}
            placeholder="openai_api_key"
            required
          />
        </label>
        <label htmlFor="provider-key-value" className="grid gap-1 text-sm text-fg">
          Secret value
          <Input
            id="provider-key-value"
            type="password"
            value={value}
            onChange={(event) => setValue(event.currentTarget.value)}
            required
          />
        </label>
        <div className="flex items-end">
          <Button type="submit" disabled={saving}>
            {saving ? 'Adding' : 'Add provider key'}
          </Button>
        </div>
      </form>

      <div className="overflow-auto rounded-md border border-border">
        <table className="w-full text-sm">
          <thead className="bg-panel-muted text-left text-fg-muted">
            <tr>
              <th className="p-2">Secret</th>
              <th className="p-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td className="p-2 text-fg-muted" colSpan={2}>No provider keys configured.</td>
              </tr>
            ) : rows.map((row) => (
              <tr key={`${row.namespace}/${row.key}`} className="border-t border-border">
                <td className="p-2 font-mono text-fg">{row.namespace}/{row.key}</td>
                <td className="p-2 text-right">
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    onClick={() => void removeSecret(row.key)}
                    disabled={deletingKey === row.key}
                  >
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
