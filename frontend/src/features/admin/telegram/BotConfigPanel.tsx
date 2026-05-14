import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

interface TelegramConfig {
  webhook_url: string;
  webhook_status: 'set' | 'retrying' | 'failed';
  token_set: boolean;
}

const CONFIG_QUERY_KEY = ['telegram-config'];

export function BotConfigPanel(): React.JSX.Element {
  const qc = useQueryClient();
  const { data: config, isError: configError } = useQuery<TelegramConfig>({
    queryKey: CONFIG_QUERY_KEY,
    queryFn: () => adminFetch<TelegramConfig>('/api/admin/telegram/config'),
  });

  const [token, setToken] = React.useState('');
  const [publicUrl, setPublicUrl] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [testChatId, setTestChatId] = React.useState('');
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<string | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch('/api/admin/telegram/config', {
        method: 'PUT',
        headers: { 'X-Confirm-Nonce': nonce },
        body: JSON.stringify({ bot_token: token, public_base_url: publicUrl }),
      });
      await qc.invalidateQueries({ queryKey: CONFIG_QUERY_KEY });
      setToken('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function sendTest(): Promise<void> {
    setTesting(true);
    setTestResult(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch('/api/admin/telegram/test-message', {
        method: 'POST',
        headers: { 'X-Confirm-Nonce': nonce },
        body: JSON.stringify({ chat_id: testChatId ? Number(testChatId) : undefined }),
      });
      setTestResult('Test message sent.');
    } catch {
      setTestResult('Failed to send test message.');
    } finally {
      setTesting(false);
    }
  }

  const statusColor =
    config?.webhook_status === 'set'
      ? 'text-positive'
      : config?.webhook_status === 'retrying'
        ? 'text-warning'
        : 'text-negative';

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-4">
      <h3 className="text-sm font-semibold text-fg">Bot Configuration</h3>
      {configError && <p className="text-sm text-negative">Failed to load configuration.</p>}
      {config && (
        <p className="text-xs text-fg-muted">
          Webhook: <span className="font-mono">{config.webhook_url || '(not set)'}</span>
          {' — '}
          <span className={statusColor}>{config.webhook_status}</span>
        </p>
      )}
      {error && <p className="text-sm text-negative">{error}</p>}
      <div className="grid gap-1 text-sm">
        <label htmlFor="tg-bot-token">Bot Token (leave blank to keep existing)</label>
        <Input
          id="tg-bot-token"
          type="password"
          value={token}
          onChange={e => setToken(e.currentTarget.value)}
          placeholder="123456:ABC..."
        />
      </div>
      <div className="grid gap-1 text-sm">
        <label htmlFor="tg-public-url">Public Base URL</label>
        <Input
          id="tg-public-url"
          value={publicUrl}
          onChange={e => setPublicUrl(e.currentTarget.value)}
          placeholder="https://dashboard.example.com"
        />
      </div>
      <Button type="button" onClick={() => void save()} disabled={saving}>
        {saving ? 'Saving…' : 'Save & rotate webhook secret'}
      </Button>
      <div className="mt-2 grid gap-1 text-sm">
        <label htmlFor="tg-test-chat-id">Send test message</label>
        <div className="flex gap-2">
          <Input
            id="tg-test-chat-id"
            value={testChatId}
            onChange={e => setTestChatId(e.currentTarget.value)}
            placeholder="Chat ID (optional)"
            className="w-48"
          />
          <Button type="button" onClick={() => void sendTest()} disabled={testing}>
            {testing ? 'Sending…' : 'Send'}
          </Button>
        </div>
      </div>
      {testResult && <p className="text-sm text-fg-muted">{testResult}</p>}
    </div>
  );
}
