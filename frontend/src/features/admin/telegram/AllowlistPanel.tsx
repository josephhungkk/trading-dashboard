import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

interface AllowlistEntry {
  chat_id: number;
  from_user_id: number;
  jwt_subject: string;
  label: string;
}

const QUERY_KEY = ['telegram-allowlist'];

export function AllowlistPanel(): React.JSX.Element {
  const qc = useQueryClient();

  const { data: entries = [], isError, isLoading } = useQuery<AllowlistEntry[]>({
    queryKey: QUERY_KEY,
    queryFn: () => adminFetch<AllowlistEntry[]>('/api/admin/telegram/allowlist'),
  });

  const [chatId, setChatId] = React.useState('');
  const [fromUserId, setFromUserId] = React.useState('');
  const [jwtSubject, setJwtSubject] = React.useState('');
  const [label, setLabel] = React.useState('');
  const [adding, setAdding] = React.useState(false);
  const [addError, setAddError] = React.useState<string | null>(null);
  const [removeError, setRemoveError] = React.useState<string | null>(null);

  async function add(): Promise<void> {
    setAdding(true);
    setAddError(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch('/api/admin/telegram/allowlist', {
        method: 'POST',
        headers: { 'X-Confirm-Nonce': nonce },
        body: JSON.stringify({
          chat_id: Number(chatId),
          from_user_id: Number(fromUserId),
          jwt_subject: jwtSubject,
          label,
        }),
      });
      setChatId('');
      setFromUserId('');
      setJwtSubject('');
      setLabel('');
      await qc.invalidateQueries({ queryKey: QUERY_KEY });
    } catch (err) {
      setAddError(err instanceof Error ? err.message : 'Add failed');
    } finally {
      setAdding(false);
    }
  }

  async function remove(entry: AllowlistEntry): Promise<void> {
    setRemoveError(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch(`/api/admin/telegram/allowlist/${entry.chat_id}`, {
        method: 'DELETE',
        headers: { 'X-Confirm-Nonce': nonce },
      });
      await qc.invalidateQueries({ queryKey: QUERY_KEY });
    } catch {
      setRemoveError('Remove failed.');
    }
  }

  return (
    <div className="grid gap-3 rounded-md border border-border bg-panel p-4">
      <h3 className="text-sm font-semibold text-fg">Allowlist</h3>
      {isError && <p className="text-sm text-negative">Failed to load allowlist.</p>}
      {removeError && <p className="text-sm text-negative">{removeError}</p>}
      {isLoading ? (
        <p className="text-xs text-fg-muted">Loading…</p>
      ) : entries.length === 0 ? (
        <p className="text-xs text-fg-muted">No entries.</p>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-fg-muted">
              <th className="pb-1 pr-3">Chat ID</th>
              <th className="pb-1 pr-3">User ID</th>
              <th className="pb-1 pr-3">JWT Subject</th>
              <th className="pb-1 pr-3">Label</th>
              <th className="pb-1" />
            </tr>
          </thead>
          <tbody>
            {entries.map(e => (
              <tr key={`${e.chat_id}-${e.from_user_id}`} className="border-t border-border">
                <td className="py-1 pr-3 font-mono">{e.chat_id}</td>
                <td className="py-1 pr-3 font-mono">{e.from_user_id}</td>
                <td className="py-1 pr-3">{e.jwt_subject}</td>
                <td className="py-1 pr-3">{e.label}</td>
                <td className="py-1">
                  <Button
                    type="button"
                    onClick={() => void remove(e)}
                    className="text-xs text-negative"
                  >
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-2 grid gap-2">
        <p className="text-xs font-medium text-fg-muted">Add entry</p>
        {addError && <p className="text-xs text-negative">{addError}</p>}
        <div className="flex flex-wrap gap-2">
          <Input
            value={chatId}
            onChange={e => setChatId(e.currentTarget.value)}
            placeholder="Chat ID"
            className="w-32"
          />
          <Input
            value={fromUserId}
            onChange={e => setFromUserId(e.currentTarget.value)}
            placeholder="User ID"
            className="w-32"
          />
          <Input
            value={jwtSubject}
            onChange={e => setJwtSubject(e.currentTarget.value)}
            placeholder="JWT subject"
            className="w-40"
          />
          <Input
            value={label}
            onChange={e => setLabel(e.currentTarget.value)}
            placeholder="Label"
            className="w-32"
          />
          <Button type="button" onClick={() => void add()} disabled={adding || !chatId || !label}>
            {adding ? 'Adding…' : 'Add'}
          </Button>
        </div>
      </div>
    </div>
  );
}
