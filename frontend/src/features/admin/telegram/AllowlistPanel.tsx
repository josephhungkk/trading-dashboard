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

function parsePositiveInt(raw: string): number | null {
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

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
  const [removing, setRemoving] = React.useState<Set<number>>(new Set());
  const [removeError, setRemoveError] = React.useState<string | null>(null);

  async function add(): Promise<void> {
    const parsedChatId = parsePositiveInt(chatId);
    const parsedFromUserId = parsePositiveInt(fromUserId);
    if (parsedChatId === null) {
      setAddError('Chat ID must be a positive integer.');
      return;
    }
    if (parsedFromUserId === null) {
      setAddError('User ID must be a positive integer.');
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      const nonce = await mintCsrfNonce();
      await adminFetch('/api/admin/telegram/allowlist', {
        method: 'POST',
        headers: { 'X-Confirm-Nonce': nonce },
        body: JSON.stringify({
          chat_id: parsedChatId,
          from_user_id: parsedFromUserId,
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
    if (removing.has(entry.chat_id)) return;
    setRemoving(prev => new Set(prev).add(entry.chat_id));
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
    } finally {
      setRemoving(prev => {
        const next = new Set(prev);
        next.delete(entry.chat_id);
        return next;
      });
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
              <tr key={e.chat_id} className="border-t border-border">
                <td className="py-1 pr-3 font-mono">{e.chat_id}</td>
                <td className="py-1 pr-3 font-mono">{e.from_user_id}</td>
                <td className="py-1 pr-3">{e.jwt_subject}</td>
                <td className="py-1 pr-3">{e.label}</td>
                <td className="py-1">
                  <Button
                    type="button"
                    onClick={() => void remove(e)}
                    disabled={removing.has(e.chat_id)}
                    className="text-xs text-negative"
                  >
                    {removing.has(e.chat_id) ? 'Removing…' : 'Remove'}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <fieldset className="mt-2 grid gap-2">
        <legend className="text-xs font-medium text-fg-muted">Add entry</legend>
        {addError && <p className="text-xs text-negative">{addError}</p>}
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
          <div className="grid gap-1 text-xs">
            <label htmlFor="tg-al-chat-id">Chat ID</label>
            <Input
              id="tg-al-chat-id"
              value={chatId}
              onChange={e => setChatId(e.currentTarget.value)}
              placeholder="e.g. 123456789"
              className="w-32"
            />
          </div>
          <div className="grid gap-1 text-xs">
            <label htmlFor="tg-al-user-id">User ID</label>
            <Input
              id="tg-al-user-id"
              value={fromUserId}
              onChange={e => setFromUserId(e.currentTarget.value)}
              placeholder="e.g. 987654321"
              className="w-32"
            />
          </div>
          <div className="grid gap-1 text-xs">
            <label htmlFor="tg-al-jwt-subject">JWT subject</label>
            <Input
              id="tg-al-jwt-subject"
              value={jwtSubject}
              onChange={e => setJwtSubject(e.currentTarget.value)}
              placeholder="user@example.com"
              className="w-40"
            />
          </div>
          <div className="grid gap-1 text-xs">
            <label htmlFor="tg-al-label">Label</label>
            <Input
              id="tg-al-label"
              value={label}
              onChange={e => setLabel(e.currentTarget.value)}
              placeholder="Alice"
              className="w-32"
            />
          </div>
        </div>
        <Button
          type="button"
          onClick={() => void add()}
          disabled={adding || !chatId || !fromUserId || !label}
        >
          {adding ? 'Adding…' : 'Add'}
        </Button>
      </fieldset>
    </div>
  );
}
