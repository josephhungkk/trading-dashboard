import * as React from 'react';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

interface TelegramConfig {
  webhook_url: string;
  webhook_status: 'set' | 'retrying' | 'failed';
  token_set: boolean;
}

export function BotConfigPanel(): React.JSX.Element {
  const [token, setToken] = React.useState('');
  const [publicUrl, setPublicUrl] = React.useState('');
  const [config, setConfig] = React.useState<TelegramConfig | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [testChatId, setTestChatId] = React.useState('');
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<string | null>(null);

  React.useEffect(() => {
    void adminFetch<TelegramConfig>('/api/admin/telegram/config').then(setConfig).catch(() => null);
  }, []);

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
      const updated = await adminFetch<TelegramConfig>('/api/admin/telegram/config');
      setConfig(updated);
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
      <div className="mt-2 flex gap-2">
        <Input
          value={testChatId}
          onChange={e => setTestChatId(e.currentTarget.value)}
          placeholder="Chat ID (optional)"
          className="w-48"
        />
        <Button type="button" onClick={() => void sendTest()} disabled={testing}>
          {testing ? 'Sending…' : 'Send test message'}
        </Button>
      </div>
      {testResult && <p className="text-sm text-fg-muted">{testResult}</p>}
    </div>
  );
}
