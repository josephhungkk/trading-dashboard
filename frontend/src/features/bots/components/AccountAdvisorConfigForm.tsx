import * as React from 'react';
import { mintCsrfNonce } from '../../../services/admin/api';
import { putAccountAdvisorConfig } from '@/services/advisor/api';
import { ADVISOR_MODES } from '@/services/advisor/types';
import type { AccountAdvisorConfigOverride, AdvisorMode } from '@/services/advisor/types';

interface Props {
  botId: string;
  account: {
    account_id: string;
    advisor_config_override: Record<string, unknown> | null;
  };
  botConfig: Record<string, unknown>;
  onSaved: () => void;
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function numberString(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
}

function isAdvisorMode(value: string): value is AdvisorMode {
  return (ADVISOR_MODES as readonly string[]).includes(value);
}

export function AccountAdvisorConfigForm(props: Props): React.JSX.Element {
  // Use key on account_id to remount when account identity changes, avoiding setState-in-effect
  return <AccountAdvisorConfigFormInner key={props.account.account_id} {...props} />;
}

function AccountAdvisorConfigFormInner({
  botId,
  account,
  botConfig,
  onSaved,
}: Props): React.JSX.Element {
  const override = account.advisor_config_override;
  const hasOverride = override != null;
  const [saving, setSaving] = React.useState(false);
  const [mode, setMode] = React.useState(() => stringValue(override?.mode));
  const [localOnly, setLocalOnly] = React.useState(() =>
    booleanValue(override?.local_only, booleanValue(botConfig.local_only, false)),
  );
  const [timeoutMs, setTimeoutMs] = React.useState(() => numberString(override?.timeout_ms));

  const effectiveMode = mode === '' ? stringValue(botConfig.mode) || 'OFF' : mode;

  async function saveOverride(): Promise<void> {
    const body: AccountAdvisorConfigOverride = { local_only: localOnly };
    if (mode !== '' && isAdvisorMode(mode)) body.mode = mode;
    if (timeoutMs !== '') body.timeout_ms = Number(timeoutMs);

    setSaving(true);
    try {
      const nonce = await mintCsrfNonce();
      await putAccountAdvisorConfig(
        botId,
        account.account_id,
        { advisor_config_override: body },
        nonce,
      );
      onSaved();
    } finally {
      setSaving(false);
    }
  }

  async function clearOverride(): Promise<void> {
    setSaving(true);
    try {
      const nonce = await mintCsrfNonce();
      await putAccountAdvisorConfig(
        botId,
        account.account_id,
        { advisor_config_override: null },
        nonce,
      );
      onSaved();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3 rounded border border-border p-3">
      {!hasOverride && <p className="text-sm text-muted-foreground">Using bot default</p>}

      <label className="flex flex-col gap-1 text-sm" htmlFor={`advisor-account-mode-${account.account_id}`}>
        <span className="text-muted-foreground">Mode</span>
        <select
          id={`advisor-account-mode-${account.account_id}`}
          value={mode}
          onChange={(event) => setMode(event.target.value)}
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        >
          <option value="">Bot default</option>
          {ADVISOR_MODES.map((advisorMode) => (
            <option key={advisorMode} value={advisorMode}>{advisorMode}</option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-sm" htmlFor={`advisor-account-local-${account.account_id}`}>
        <input
          id={`advisor-account-local-${account.account_id}`}
          type="checkbox"
          checked={localOnly}
          onChange={(event) => setLocalOnly(event.target.checked)}
          className="rounded border-border"
        />
        <span className="text-muted-foreground">Local only</span>
      </label>

      <label className="flex flex-col gap-1 text-sm" htmlFor={`advisor-account-timeout-${account.account_id}`}>
        <span className="text-muted-foreground">Timeout ms</span>
        <input
          id={`advisor-account-timeout-${account.account_id}`}
          type="number"
          min="100"
          max="10000"
          value={timeoutMs}
          onChange={(event) => setTimeoutMs(event.target.value)}
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        />
      </label>

      <div className="rounded bg-muted p-2 text-xs">
        <p>Effective mode: {effectiveMode}</p>
        <p>Effective local_only: {String(localOnly)}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void saveOverride()}
          disabled={saving}
          className="btn-primary text-xs"
        >
          {saving ? 'Saving…' : 'Save override'}
        </button>
        {hasOverride && (
          <button
            type="button"
            onClick={() => void clearOverride()}
            disabled={saving}
            className="btn-secondary text-xs"
          >
            Clear override
          </button>
        )}
      </div>
    </div>
  );
}
