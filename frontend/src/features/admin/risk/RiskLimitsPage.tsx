/**
 * Phase 10a E3 — admin CRUD page for risk_limits rows.
 *
 * Lists every row from `useRiskLimits()`. The "New limit" button opens
 * a Dialog with the create/edit form. Delete prompts a confirm and
 * fires the remove mutation. All mutations invalidate the ['risk-limits']
 * query key inside the hook so the table re-renders on success.
 */

import * as React from 'react';
import { useRiskLimits } from '@/hooks/useRiskLimits';
import { Button } from '@/components/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/primitives/Dialog';
import { Input } from '@/components/primitives/Input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/primitives/Select';
import { Switch } from '@/components/primitives/Switch';
import type {
  RiskLimitKind,
  RiskLimitOut,
  RiskScopeType,
} from '@/services/risk/types';

const SCOPE_TYPES: readonly RiskScopeType[] = ['global', 'broker', 'account'];

const LIMIT_KINDS: readonly { value: RiskLimitKind; label: string }[] = [
  { value: 'max_daily_loss_currency_base', label: 'Max daily loss (currency base)' },
  { value: 'max_position_concentration_pct', label: 'Max position concentration %' },
  { value: 'pdt_warn_remaining', label: 'PDT warn remaining' },
  { value: 'min_buying_power_buffer_pct', label: 'Min buying-power buffer %' },
];

interface FormState {
  scope_type: RiskScopeType;
  scope_id: string;
  limit_kind: RiskLimitKind;
  limit_value: string;
  warn_at_pct: string;
  is_active: boolean;
  notes: string;
}

const INITIAL_FORM: FormState = {
  scope_type: 'global',
  scope_id: '',
  limit_kind: 'max_daily_loss_currency_base',
  limit_value: '',
  warn_at_pct: '',
  is_active: true,
  notes: '',
};

function rowToFormState(row: RiskLimitOut): FormState {
  return {
    scope_type: row.scope_type as RiskScopeType,
    scope_id: row.scope_id ?? '',
    limit_kind: row.limit_kind as RiskLimitKind,
    limit_value: row.limit_value,
    warn_at_pct: row.warn_at_pct ?? '',
    is_active: row.is_active,
    notes: row.notes,
  };
}

function formStateToBody(form: FormState): {
  scope_type: RiskScopeType;
  scope_id: string | null;
  limit_kind: RiskLimitKind;
  limit_value: string;
  warn_at_pct: string | null;
  is_active: boolean;
  notes: string;
} {
  return {
    scope_type: form.scope_type,
    scope_id: form.scope_type === 'global' ? null : form.scope_id.trim(),
    limit_kind: form.limit_kind,
    limit_value: form.limit_value,
    warn_at_pct: form.warn_at_pct.trim() === '' ? null : form.warn_at_pct,
    is_active: form.is_active,
    notes: form.notes,
  };
}

const DECIMAL_REGEX = /^\d+(\.\d+)?$/;

export function RiskLimitsPage(): React.JSX.Element {
  const { list, create, update, remove } = useRiskLimits();
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editId, setEditId] = React.useState<number | null>(null);
  const [form, setForm] = React.useState<FormState>(INITIAL_FORM);

  const openCreate = (): void => {
    setEditId(null);
    setForm(INITIAL_FORM);
    setDialogOpen(true);
  };

  const openEdit = (row: RiskLimitOut): void => {
    setEditId(row.id);
    setForm(rowToFormState(row));
    setDialogOpen(true);
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!DECIMAL_REGEX.test(form.limit_value)) return;
    if (form.warn_at_pct !== '' && !DECIMAL_REGEX.test(form.warn_at_pct)) return;
    if (form.scope_type !== 'global' && form.scope_id.trim() === '') return;
    const body = formStateToBody(form);
    const onDone = { onSuccess: (): void => setDialogOpen(false) };
    if (editId !== null) {
      update.mutate({ id: editId, body }, onDone);
    } else {
      create.mutate(body, onDone);
    }
  };

  const handleDelete = (row: RiskLimitOut): void => {
    const ok = window.confirm(
      `Deactivate risk limit #${row.id} (${row.limit_kind})?`,
    );
    if (!ok) return;
    remove.mutate(row.id);
  };

  return (
    <section className="flex flex-col gap-4 p-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Risk limits</h1>
        <Button type="button" onClick={openCreate}>New limit</Button>
      </header>

      {list.isLoading ? (
        <p className="text-sm text-fg-muted">Loading risk limits…</p>
      ) : list.error ? (
        <p role="alert" className="text-sm text-destructive">{list.error.message}</p>
      ) : list.data && list.data.length === 0 ? (
        <p className="text-sm text-fg-muted">No risk limits configured.</p>
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel-muted text-left text-fg-muted">
              <tr>
                <th className="p-2">Scope</th>
                <th className="p-2">Kind</th>
                <th className="p-2">Value</th>
                <th className="p-2">Warn %</th>
                <th className="p-2">Active</th>
                <th className="p-2">Updated by</th>
                <th className="p-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.map((row) => (
                <tr key={row.id} className="border-t border-border">
                  <td className="p-2">
                    {row.scope_type}
                    {row.scope_id ? `:${row.scope_id}` : ''}
                  </td>
                  <td className="p-2">{row.limit_kind}</td>
                  <td className="p-2 font-mono">{row.limit_value}</td>
                  <td className="p-2 font-mono">{row.warn_at_pct ?? '—'}</td>
                  <td className="p-2">{row.is_active ? 'active' : 'inactive'}</td>
                  <td className="p-2">{row.updated_by}</td>
                  <td className="p-2">
                    <div className="flex gap-2">
                      <Button type="button" variant="outline" onClick={() => openEdit(row)}>
                        Edit
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => handleDelete(row)}
                        disabled={!row.is_active || remove.isPending}
                      >
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editId === null ? 'Create risk limit' : `Edit risk limit #${editId}`}</DialogTitle>
          </DialogHeader>
          <form className="flex flex-col gap-3 text-sm" onSubmit={handleSubmit}>
            <label htmlFor="risk-limit-scope-type" className="flex flex-col gap-1">
              <span className="font-medium">Scope type</span>
              <Select
                value={form.scope_type}
                onValueChange={(value) =>
                  setForm((current) => ({ ...current, scope_type: value as RiskScopeType }))
                }
              >
                <SelectTrigger id="risk-limit-scope-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SCOPE_TYPES.map((scope) => (
                    <SelectItem key={scope} value={scope}>{scope}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>

            <label htmlFor="risk-limit-scope-id" className="flex flex-col gap-1">
              <span className="font-medium">Scope id</span>
              <Input
                id="risk-limit-scope-id"
                value={form.scope_id}
                onChange={(event) =>
                  setForm((current) => ({ ...current, scope_id: event.currentTarget.value }))
                }
                disabled={form.scope_type === 'global'}
                placeholder={form.scope_type === 'global' ? 'n/a — global scope' : 'broker_id or account UUID'}
              />
            </label>

            <label htmlFor="risk-limit-kind" className="flex flex-col gap-1">
              <span className="font-medium">Limit kind</span>
              <Select
                value={form.limit_kind}
                onValueChange={(value) =>
                  setForm((current) => ({ ...current, limit_kind: value as RiskLimitKind }))
                }
              >
                <SelectTrigger id="risk-limit-kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LIMIT_KINDS.map((kind) => (
                    <SelectItem key={kind.value} value={kind.value}>{kind.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>

            <label htmlFor="risk-limit-value" className="flex flex-col gap-1">
              <span className="font-medium">Limit value</span>
              <Input
                id="risk-limit-value"
                value={form.limit_value}
                onChange={(event) =>
                  setForm((current) => ({ ...current, limit_value: event.currentTarget.value }))
                }
                pattern={DECIMAL_REGEX.source}
                placeholder="e.g. 1000.00"
                required
              />
            </label>

            <label htmlFor="risk-limit-warn-at" className="flex flex-col gap-1">
              <span className="font-medium">Warn at % (optional)</span>
              <Input
                id="risk-limit-warn-at"
                value={form.warn_at_pct}
                onChange={(event) =>
                  setForm((current) => ({ ...current, warn_at_pct: event.currentTarget.value }))
                }
                placeholder="e.g. 80"
              />
            </label>

            <label htmlFor="risk-limit-active" className="flex items-center gap-2">
              <Switch
                id="risk-limit-active"
                checked={form.is_active}
                onCheckedChange={(checked) =>
                  setForm((current) => ({ ...current, is_active: checked }))
                }
              />
              <span>Active</span>
            </label>

            <label htmlFor="risk-limit-notes" className="flex flex-col gap-1">
              <span className="font-medium">Notes</span>
              <textarea
                id="risk-limit-notes"
                value={form.notes}
                onChange={(event) =>
                  setForm((current) => ({ ...current, notes: event.currentTarget.value }))
                }
                maxLength={1000}
                rows={3}
                className="w-full rounded-md border border-border bg-panel p-2 text-sm"
              />
            </label>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={create.isPending || update.isPending}
              >
                {editId === null
                  ? create.isPending ? 'Creating…' : 'Create'
                  : update.isPending ? 'Saving…' : 'Save'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}
