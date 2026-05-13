import * as React from 'react';
import { useState } from 'react';

import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

interface Props {
  initialUrl?: string;
  initialSecret?: string;
  webhookId: string;
  onSaved?: () => void;
}

export function WebhookConfigPanel({
  initialUrl,
  initialSecret,
  webhookId,
  onSaved,
}: Props): React.JSX.Element {
  const [url, setUrl] = useState(initialUrl ?? '');
  const [secret, setSecret] = useState(initialSecret ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setError(null);
    try {
      if (!url.startsWith('https://')) {
        setError('Webhook URL must use https://');
        return;
      }
      const nonce = await mintCsrfNonce();
      await adminFetch(`/api/admin/alerts/webhooks/${webhookId}`, {
        method: 'PUT',
        body: JSON.stringify({ url, secret }),
        headers: { 'X-Confirm-Nonce': nonce },
      });
      onSaved?.();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      className="flex flex-col gap-3 rounded-md border border-border bg-panel p-4"
      data-testid="webhook-config-panel"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Webhook (id: {webhookId})
      </h2>
      <label className="flex flex-col gap-1 text-sm">
        <span>URL (https only)</span>
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          placeholder="https://hooks.example.com/alerts"
          data-testid="webhook-url"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span>HMAC secret</span>
        <input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm font-mono"
          autoComplete="new-password"
          data-testid="webhook-secret"
        />
      </label>
      {error && (
        <p role="alert" className="text-xs text-red-600">
          {error}
        </p>
      )}
      <button
        type="button"
        onClick={() => {
          void handleSave();
        }}
        disabled={saving}
        className="self-start rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
        data-testid="webhook-save"
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
    </section>
  );
}
