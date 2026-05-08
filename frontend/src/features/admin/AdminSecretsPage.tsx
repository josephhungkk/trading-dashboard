import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { DataTable } from '@/components/patterns/DataTable';
import { MobileCardRow } from '@/components/patterns/MobileCardRow';
import { Badge } from '@/components/primitives/Badge';
import { Button } from '@/components/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/primitives/Dialog';
import { Input } from '@/components/primitives/Input';
import {
  deleteSecret,
  listSecrets,
  revealSecret,
  upsertSecret,
  type ConfigValue,
  type ConfigValueType,
  type SecretMetadata,
} from '@/services/admin-api';

const VALUE_TYPES: readonly ConfigValueType[] = ['str', 'int', 'bool', 'json'];

interface SecretFormState {
  namespace: string;
  key: string;
  value_type: ConfigValueType;
  value: string;
}

export function AdminSecretsPage(): React.JSX.Element {
  const [rows, setRows] = React.useState<SecretMetadata[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState<SecretMetadata | null>(null);
  const [editOpen, setEditOpen] = React.useState(false);
  const [revealRow, setRevealRow] = React.useState<SecretMetadata | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listSecrets());
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- load is async; setState is called in .then/.catch, not synchronously
  React.useEffect(() => { void load(); }, [load]);

  const onDelete = React.useCallback(async (row: SecretMetadata): Promise<void> => {
    setError(null);
    try {
      await deleteSecret(row.namespace, row.key);
      await load();
    } catch (err) {
      setError(messageFrom(err));
    }
  }, [load]);

  async function onSave(state: SecretFormState): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      await upsertSecret({
        namespace: state.namespace,
        key: state.key,
        value_type: state.value_type,
        value: parseValue(state.value, state.value_type),
      });
      setEditOpen(false);
      setEditing(null);
      await load();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setSaving(false);
    }
  }

  const columns = React.useMemo<ColumnDef<SecretMetadata>[]>(
    () => [
      { accessorKey: 'namespace', header: 'Namespace' },
      { accessorKey: 'key', header: 'Key' },
      {
        accessorKey: 'value_type',
        header: 'Type',
        cell: ({ row }) => <Badge>{row.original.value_type}</Badge>,
      },
      {
        accessorKey: 'updated_at',
        header: 'Updated',
        cell: ({ row }) => <span className="text-fg-muted">{formatDate(row.original.updated_at)}</span>,
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setRevealRow(row.original)}>
              Reveal
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setEditing(row.original);
                setEditOpen(true);
              }}
            >
              Edit
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={() => void onDelete(row.original)}
            >
              Delete
            </Button>
          </div>
        ),
      },
    ],
    [onDelete],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-fg-muted">{loading ? 'Loading secrets' : `${rows.length} secret row(s)`}</p>
        <Button
          type="button"
          onClick={() => {
            setEditing(null);
            setEditOpen(true);
          }}
        >
          New
        </Button>
      </header>

      {error && <div className="rounded-md border border-negative bg-negative/10 p-3 text-sm text-negative">{error}</div>}

      <div className="min-h-0 flex-1 rounded-lg border border-border bg-panel">
        <DataTable<SecretMetadata>
          columns={columns}
          data={rows}
          rowKey={(row) => `${row.namespace}.${row.key}`}
          mobileRow={(row) => (
            <MobileCardRow
              primary={`${row.namespace}.${row.key}`}
              metrics={[
                { label: 'Type', value: row.value_type },
                { label: 'Updated', value: formatDate(row.updated_at) },
              ]}
            />
          )}
        />
      </div>

      <SecretEditDialog
        key={`${editOpen ? 'open' : 'closed'}:${editing ? `${editing.namespace}.${editing.key}` : 'new'}`}
        open={editOpen}
        row={editing}
        saving={saving}
        onOpenChange={(open) => {
          setEditOpen(open);
          if (!open) setEditing(null);
        }}
        onSave={onSave}
      />
      <RevealDialog row={revealRow} onOpenChange={(open) => {
        if (!open) setRevealRow(null);
      }} />
    </div>
  );
}

function SecretEditDialog({
  open,
  row,
  saving,
  onOpenChange,
  onSave,
}: {
  open: boolean;
  row: SecretMetadata | null;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (state: SecretFormState) => Promise<void>;
}): React.JSX.Element {
  const [state, setState] = React.useState<SecretFormState>(() => formState(row));

  function submit(e: React.FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    void onSave(state);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{row ? 'Edit Secret' : 'New Secret'}</DialogTitle>
            <DialogDescription>Secret values are written through the admin API.</DialogDescription>
          </DialogHeader>

          <label htmlFor="secret-namespace" className="grid gap-1 text-sm text-fg">
            Namespace
            <Input
              id="secret-namespace"
              value={state.namespace}
              disabled={Boolean(row)}
              onChange={(e) => setState({ ...state, namespace: e.currentTarget.value })}
              required
            />
          </label>
          <label htmlFor="secret-key" className="grid gap-1 text-sm text-fg">
            Key
            <Input
              id="secret-key"
              value={state.key}
              disabled={Boolean(row)}
              onChange={(e) => setState({ ...state, key: e.currentTarget.value })}
              required
            />
          </label>
          <label htmlFor="secret-value-type" className="grid gap-1 text-sm text-fg">
            Type
            <select
              id="secret-value-type"
              value={state.value_type}
              onChange={(e) => setState({ ...state, value_type: e.currentTarget.value as ConfigValueType })}
              className="h-10 rounded-md border border-border bg-panel p-2 text-sm text-fg"
            >
              {VALUE_TYPES.map((valueType) => (
                <option key={valueType} value={valueType}>{valueType}</option>
              ))}
            </select>
          </label>
          <label htmlFor="secret-value" className="grid gap-1 text-sm text-fg">
            Value
            <Input
              id="secret-value"
              value={state.value}
              onChange={(e) => setState({ ...state, value: e.currentTarget.value })}
              required
            />
          </label>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RevealDialog({
  row,
  onOpenChange,
}: {
  row: SecretMetadata | null;
  onOpenChange: (open: boolean) => void;
}): React.JSX.Element {
  const revealKey = row ? `${row.namespace}.${row.key}` : null;
  const [result, setResult] = React.useState<{
    key: string;
    value: string;
    error: string | null;
  } | null>(null);
  const value = result?.key === revealKey ? result.value : '';
  const error = result?.key === revealKey ? result.error : null;
  const loading = Boolean(row) && result?.key !== revealKey;

  React.useEffect(() => {
    let active = true;
    if (!row || !revealKey) return undefined;
    void revealSecret(row.namespace, row.key)
      .then((data) => {
        if (active) {
          setResult({
            key: revealKey,
            value: formatConfigValue(data.value, data.value_type),
            error: null,
          });
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setResult({
            key: revealKey,
            value: '',
            error: messageFrom(err),
          });
        }
      });
    return () => {
      active = false;
    };
  }, [revealKey, row]);

  function close(open: boolean): void {
    onOpenChange(open);
  }

  return (
    <Dialog open={Boolean(row)} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reveal Secret</DialogTitle>
          <DialogDescription>{row ? `${row.namespace}.${row.key}` : ''}</DialogDescription>
        </DialogHeader>

        {error && <div className="rounded-md border border-negative bg-negative/10 p-3 text-sm text-negative">{error}</div>}
        <Input value={loading ? 'Loading secret' : value} readOnly />

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => close(false)}>
            Close
          </Button>
          <Button
            type="button"
            disabled={!value}
            onClick={() => {
              void navigator.clipboard.writeText(value);
            }}
          >
            Copy
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function formState(row: SecretMetadata | null): SecretFormState {
  return {
    namespace: row?.namespace ?? '',
    key: row?.key ?? '',
    value_type: row?.value_type ?? 'str',
    value: '',
  };
}

function parseValue(value: string, valueType: ConfigValueType): ConfigValue {
  if (valueType === 'int') return Number.parseInt(value, 10);
  if (valueType === 'bool') return value === 'true';
  if (valueType === 'json') {
    try {
      return JSON.parse(value) as ConfigValue;
    } catch (err) {
      throw new Error(`Invalid JSON: ${err instanceof Error ? err.message : 'parse failed'}`);
    }
  }
  return value;
}

function formatConfigValue(value: ConfigValue, valueType: ConfigValueType): string {
  if (valueType === 'json') return JSON.stringify(value);
  if (typeof value === 'string') return value;
  return String(value);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function messageFrom(err: unknown): string {
  return err instanceof Error ? err.message : 'Admin request failed';
}
