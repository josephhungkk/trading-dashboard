import * as React from 'react';
import { Button } from '@/components/primitives/Button';
import { AdminApiError, adminFetch, mintCsrfNonce } from '@/services/admin/api';

const NAMESPACE = 'ai_router';
const KEY = 'capability_map';

type JsonValue = string | number | boolean | null | { [key: string]: JsonValue } | JsonValue[];

interface ConfigRecord {
  namespace: string;
  key: string;
  value: JsonValue;
  value_type: 'json';
}

function readCapabilityMap(): Promise<ConfigRecord> {
  return adminFetch<ConfigRecord>(
    `/api/admin/config/${encodeURIComponent(NAMESPACE)}/${encodeURIComponent(KEY)}`,
  );
}

function createCapabilityMap(value: JsonValue, nonce: string): Promise<ConfigRecord> {
  return adminFetch<ConfigRecord>('/api/admin/config', {
    method: 'POST',
    headers: { 'X-Confirm-Nonce': nonce },
    body: JSON.stringify({
      namespace: NAMESPACE,
      key: KEY,
      value,
      value_type: 'json',
    }),
  });
}

function updateCapabilityMap(value: JsonValue, nonce: string): Promise<ConfigRecord> {
  return adminFetch<ConfigRecord>(
    `/api/admin/config/${encodeURIComponent(NAMESPACE)}/${encodeURIComponent(KEY)}`,
    {
      method: 'PUT',
      headers: { 'X-Confirm-Nonce': nonce },
      body: JSON.stringify({
        namespace: NAMESPACE,
        key: KEY,
        value,
        value_type: 'json',
      }),
    },
  );
}

export function CapabilityMapEditor(): React.JSX.Element {
  const [text, setText] = React.useState('{}');
  const [exists, setExists] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [jsonError, setJsonError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const row = await readCapabilityMap();
      setText(JSON.stringify(row.value, null, 2));
      setExists(true);
    } catch (err) {
      if (err instanceof AdminApiError && err.status === 404) {
        setText('{}');
        setExists(false);
      } else {
        setError(err instanceof Error ? err.message : 'Admin request failed');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- load is async; setState is called in .then/.catch, not synchronously
  React.useEffect(() => { void load(); }, [load]);

  React.useEffect(() => {
    const timeout = window.setTimeout(() => {
      try {
        JSON.parse(text);
        setJsonError(null);
      } catch (err) {
        setJsonError(err instanceof Error ? err.message : 'Invalid JSON');
      }
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [text]);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const value = JSON.parse(text) as JsonValue;
      const nonce = await mintCsrfNonce();
      const row = exists
        ? await updateCapabilityMap(value, nonce)
        : await createCapabilityMap(value, nonce);
      setText(JSON.stringify(row.value, null, 2));
      setExists(true);
      setJsonError(null);
      setSaved(true);
    } catch (err) {
      if (err instanceof SyntaxError) {
        setJsonError(err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Admin request failed');
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-fg-muted">
          {loading ? 'Loading capability map' : exists ? 'Editing ai_router/capability_map' : 'No row exists yet'}
        </p>
        <Button
          type="button"
          onClick={() => void save()}
          disabled={loading || saving || jsonError !== null}
        >
          {saving ? 'Saving' : 'Save capability map'}
        </Button>
      </div>

      {error && <div className="rounded-md border border-negative bg-negative/10 p-3 text-sm text-negative">{error}</div>}
      {jsonError && <p role="alert" className="text-sm text-negative">Invalid JSON: {jsonError}</p>}
      {saved && <p className="text-sm text-positive">Saved capability map.</p>}

      <label htmlFor="ai-capability-map" className="grid gap-1 text-sm text-fg">
        Capability map JSON
        <textarea
          id="ai-capability-map"
          value={text}
          onChange={(event) => {
            setText(event.currentTarget.value);
            setSaved(false);
          }}
          rows={14}
          spellCheck={false}
          className="min-h-56 w-full rounded-md border border-border bg-bg p-3 font-mono text-sm text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active"
        />
      </label>
    </div>
  );
}
