import * as React from 'react';
import { useState } from 'react';

import { ParseFailedEditor } from '@/features/alerts/ParseFailedEditor';
import { PredicateVisualiser } from '@/features/alerts/PredicateVisualiser';
import { postAlert, putPredicate, confirmAlert, deleteAlert } from '@/services/alerts/api';
import type {
  AlertRule,
  CreateAlertResponse,
  ParseFailedResponse,
} from '@/services/alerts/types';
import { isParseFailed } from '@/services/alerts/types';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated?: (rule: AlertRule) => void;
}

type Stage =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'parsed'; rule: AlertRule }
  | { kind: 'parse_failed'; resp: ParseFailedResponse }
  | { kind: 'error'; message: string };

export function CreateAlertModal({ open, onClose, onCreated }: Props): React.JSX.Element | null {
  const [originalNl, setOriginalNl] = useState('');
  const [userLabel, setUserLabel] = useState('');
  const [stage, setStage] = useState<Stage>({ kind: 'idle' });
  const [saving, setSaving] = useState(false);
  const [schemaErrors, setSchemaErrors] = useState<string[]>([]);

  if (!open) return null;

  const reset = (): void => {
    setOriginalNl('');
    setUserLabel('');
    setStage({ kind: 'idle' });
    setSchemaErrors([]);
  };

  const handleClose = (): void => {
    reset();
    onClose();
  };

  const handleParse = async (): Promise<void> => {
    if (originalNl.trim() === '' || userLabel.trim() === '') {
      setStage({ kind: 'error', message: 'Rule text and label are required.' });
      return;
    }
    setStage({ kind: 'submitting' });
    try {
      const resp: CreateAlertResponse = await postAlert({
        user_label: userLabel,
        original_nl: originalNl,
        tick_subscribed: false,
        delivery_channels: ['in_app'],
      });
      if (isParseFailed(resp)) {
        setStage({ kind: 'parse_failed', resp });
        return;
      }
      setStage({ kind: 'parsed', rule: resp });
    } catch (err) {
      setStage({ kind: 'error', message: (err as Error).message });
    }
  };

  const handleConfirm = async (): Promise<void> => {
    if (stage.kind !== 'parsed') return;
    setSaving(true);
    try {
      const confirmed = await confirmAlert(stage.rule.id);
      onCreated?.(confirmed);
      handleClose();
    } catch (err) {
      setStage({ kind: 'error', message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const handleReject = async (): Promise<void> => {
    if (stage.kind !== 'parsed') return;
    try {
      await deleteAlert(stage.rule.id);
    } catch {
      // Best-effort: rule is unconfirmed, will be swept by retention anyway.
    }
    reset();
  };

  const handleSaveParseFailedJson = async (
    predicate: Record<string, unknown>,
  ): Promise<void> => {
    setSaving(true);
    setSchemaErrors([]);
    try {
      const created = await postAlert({
        user_label: userLabel,
        original_nl: originalNl,
        predicate_json: predicate,
        tick_subscribed: false,
        delivery_channels: ['in_app'],
      });
      if (isParseFailed(created)) {
        setStage({ kind: 'parse_failed', resp: created });
        return;
      }
      const confirmed = await confirmAlert(created.id);
      onCreated?.(confirmed);
      handleClose();
    } catch (err) {
      const maybeDetail = (err as { body?: { detail?: { schema_errors?: string[] } } }).body;
      if (maybeDetail?.detail?.schema_errors) {
        setSchemaErrors(maybeDetail.detail.schema_errors);
      } else {
        setStage({ kind: 'error', message: (err as Error).message });
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Create alert"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-testid="create-alert-modal"
    >
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col gap-4 overflow-y-auto rounded-lg bg-background p-6 shadow-xl">
        <header className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">New alert</h1>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-md px-2 py-1 text-sm hover:bg-muted"
            aria-label="Close"
            data-testid="create-alert-close"
          >
            ✕
          </button>
        </header>

        <label className="flex flex-col gap-1 text-sm">
          <span>Label</span>
          <input
            type="text"
            value={userLabel}
            onChange={(e) => setUserLabel(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
            placeholder="AAPL above 200"
            data-testid="create-alert-label"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Rule text</span>
          <textarea
            value={originalNl}
            onChange={(e) => setOriginalNl(e.target.value)}
            className="min-h-[6rem] rounded-md border border-border bg-background px-3 py-2 text-sm"
            placeholder="Alert me when AAPL crosses above 200"
            data-testid="create-alert-nl"
          />
        </label>

        {stage.kind === 'error' && (
          <p
            role="alert"
            className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-900"
          >
            {stage.message}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={handleParse}
            disabled={stage.kind === 'submitting'}
            className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
            data-testid="create-alert-parse"
          >
            {stage.kind === 'submitting' ? 'Parsing…' : 'Parse'}
          </button>
        </div>

        {stage.kind === 'parsed' && (
          <section
            className="flex flex-col gap-3 rounded-md border border-border bg-panel p-4"
            data-testid="create-alert-parsed"
          >
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Parsed predicate
            </h2>
            <PredicateVisualiser predicate={stage.rule.predicate_json} />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={handleReject}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
                data-testid="create-alert-reject"
              >
                Reject
              </button>
              <button
                type="button"
                onClick={handleConfirm}
                disabled={saving}
                className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
                data-testid="create-alert-confirm"
              >
                {saving ? 'Confirming…' : 'Confirm'}
              </button>
            </div>
          </section>
        )}

        {stage.kind === 'parse_failed' && (
          <ParseFailedEditor
            partialPredicate={stage.resp.partial_predicate}
            suggestions={stage.resp.suggestions}
            onSave={(predicate) => {
              void handleSaveParseFailedJson(predicate);
            }}
            saving={saving}
            schemaErrors={schemaErrors}
          />
        )}
      </div>
    </div>
  );
}

export { putPredicate };
