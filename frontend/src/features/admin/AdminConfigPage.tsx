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
  deleteConfig,
  listConfigs,
  upsertConfig,
  type ConfigRecord,
  type ConfigValue,
  type ConfigValueType,
} from '@/services/admin-api';

const VALUE_TYPES: readonly ConfigValueType[] = ['str', 'int', 'bool', 'json'];

interface ConfigFormState {
  namespace: string;
  key: string;
  value_type: ConfigValueType;
  value: string;
}

export function AdminConfigPage(): React.JSX.Element {
  const [rows, setRows] = React.useState<ConfigRecord[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState<ConfigRecord | null>(null);
  const [dialogOpen, setDialogOpen] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listConfigs());
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    let active = true;
    void listConfigs()
      .then((data) => {
        if (active) setRows(data);
      })
      .catch((err: unknown) => {
        if (active) setError(messageFrom(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const onDelete = React.useCallback(async (row: ConfigRecord): Promise<void> => {
    setError(null);
    try {
      await deleteConfig(row.namespace, row.key);
      await load();
    } catch (err) {
      setError(messageFrom(err));
    }
  }, [load]);

  async function onSave(state: ConfigFormState): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      await upsertConfig({
        namespace: state.namespace,
        key: state.key,
        value_type: state.value_type,
        value: parseValue(state.value, state.value_type),
      });
      setDialogOpen(false);
      setEditing(null);
      await load();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setSaving(false);
    }
  }

  const columns = React.useMemo<ColumnDef<ConfigRecord>[]>(
    () => [
      { accessorKey: 'namespace', header: 'Namespace' },
      { accessorKey: 'key', header: 'Key' },
      {
        accessorKey: 'value',
        header: 'Value',
        cell: ({ row }) => <span className="font-mono text-fg">{formatValue(row.original)}</span>,
      },
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
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setEditing(row.original);
                setDialogOpen(true);
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
        <p className="text-sm text-fg-muted">{loading ? 'Loading config' : `${rows.length} config row(s)`}</p>
        <Button
          type="button"
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
        >
          New
        </Button>
      </header>

      {error && <div className="rounded-md border border-negative bg-negative/10 p-3 text-sm text-negative">{error}</div>}

      <div className="min-h-0 flex-1 rounded-lg border border-border bg-panel">
        <DataTable<ConfigRecord>
          columns={columns}
          data={rows}
          rowKey={(row) => `${row.namespace}.${row.key}`}
          mobileRow={(row) => (
            <MobileCardRow
              primary={`${row.namespace}.${row.key}`}
              secondary={formatValue(row)}
              metrics={[
                { label: 'Type', value: row.value_type },
                { label: 'Updated', value: formatDate(row.updated_at) },
              ]}
            />
          )}
        />
      </div>

      <ConfigEditDialog
        key={`${dialogOpen ? 'open' : 'closed'}:${editing ? `${editing.namespace}.${editing.key}` : 'new'}`}
        open={dialogOpen}
        row={editing}
        saving={saving}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) setEditing(null);
        }}
        onSave={onSave}
      />
    </div>
  );
}

function ConfigEditDialog({
  open,
  row,
  saving,
  onOpenChange,
  onSave,
}: {
  open: boolean;
  row: ConfigRecord | null;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (state: ConfigFormState) => Promise<void>;
}): React.JSX.Element {
  const [state, setState] = React.useState<ConfigFormState>(() => formState(row));

  function submit(e: React.FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    void onSave(state);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{row ? 'Edit Config' : 'New Config'}</DialogTitle>
            <DialogDescription>Update runtime config through the admin API.</DialogDescription>
          </DialogHeader>

          <label htmlFor="config-namespace" className="grid gap-1 text-sm text-fg">
            Namespace
            <Input
              id="config-namespace"
              value={state.namespace}
              disabled={Boolean(row)}
              onChange={(e) => setState({ ...state, namespace: e.currentTarget.value })}
              required
            />
          </label>
          <label htmlFor="config-key" className="grid gap-1 text-sm text-fg">
            Key
            <Input
              id="config-key"
              value={state.key}
              disabled={Boolean(row)}
              onChange={(e) => setState({ ...state, key: e.currentTarget.value })}
              required
            />
          </label>
          <label htmlFor="config-value-type" className="grid gap-1 text-sm text-fg">
            Type
            <select
              id="config-value-type"
              value={state.value_type}
              onChange={(e) => setState({ ...state, value_type: e.currentTarget.value as ConfigValueType })}
              className="h-10 rounded-md border border-border bg-panel p-2 text-sm text-fg"
            >
              {VALUE_TYPES.map((valueType) => (
                <option key={valueType} value={valueType}>{valueType}</option>
              ))}
            </select>
          </label>
          <label htmlFor="config-value" className="grid gap-1 text-sm text-fg">
            Value
            <Input
              id="config-value"
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

function formState(row: ConfigRecord | null): ConfigFormState {
  return {
    namespace: row?.namespace ?? '',
    key: row?.key ?? '',
    value_type: row?.value_type ?? 'str',
    value: row ? formatValue(row) : '',
  };
}

function parseValue(value: string, valueType: ConfigValueType): ConfigValue {
  if (valueType === 'int') return Number.parseInt(value, 10);
  if (valueType === 'bool') return value === 'true';
  if (valueType === 'json') return JSON.parse(value) as ConfigValue;
  return value;
}

function formatValue(row: ConfigRecord): string {
  if (row.value_type === 'json') return JSON.stringify(row.value);
  if (typeof row.value === 'string') return row.value;
  return String(row.value);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function messageFrom(err: unknown): string {
  return err instanceof Error ? err.message : 'Admin request failed';
}
