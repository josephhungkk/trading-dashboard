import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { mintCsrfNonce } from '../../../services/admin/api';
import { getAdvisorConfig, updateAdvisorConfig } from '../../../services/advisor/api';
import type { AdvisorConfig, AdvisorMode } from '../../../services/advisor/types';

interface Props {
  botId: string;
}

const MODES: AdvisorMode[] = ['OFF', 'OBSERVE', 'VETO'];

function defaultConfig(): AdvisorConfig {
  return {
    mode: 'OFF',
    capability: 'reasoning',
    local_only: false,
    timeout_ms: 3000,
    daily_budget_usd: '5.00',
    max_qps: 2,
    auto_pause_threshold: 0,
    auto_pause_window_seconds: 300,
    min_veto_confidence: 0,
  };
}

interface FormProps {
  botId: string;
  initialConfig: AdvisorConfig;
}

function AdvisorConfigFormInner({ botId, initialConfig }: FormProps): React.JSX.Element {
  const queryClient = useQueryClient();
  const [config, setConfig] = React.useState<AdvisorConfig>(initialConfig);
  const [saved, setSaved] = React.useState(false);
  const [validationError, setValidationError] = React.useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      if (config.min_veto_confidence < 0 || config.min_veto_confidence > 1) {
        throw new Error('Min veto confidence must be between 0 and 1.');
      }
      const nonce = await mintCsrfNonce();
      return updateAdvisorConfig(botId, config, nonce);
    },
    onSuccess: (response) => {
      setSaved(true);
      setValidationError(null);
      setConfig(response.config);
      void queryClient.invalidateQueries({ queryKey: ['bot', botId, 'advisor-config'] });
      window.setTimeout(() => setSaved(false), 2000);
    },
    onError: (error) => {
      setValidationError(error instanceof Error ? error.message : 'Failed to save advisor config.');
    },
  });

  function setNumber<K extends keyof AdvisorConfig>(key: K, value: string): void {
    const parsed = Number(value);
    setConfig((current) => ({
      ...current,
      [key]: Number.isFinite(parsed) ? parsed : 0,
    }));
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (config.min_veto_confidence < 0 || config.min_veto_confidence > 1) {
      setValidationError('Min veto confidence must be between 0 and 1.');
      return;
    }
    setValidationError(null);
    mutation.mutate();
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Advisor config" className="space-y-3" noValidate>
      <h2 className="text-sm font-semibold">Advisor config</h2>

      <label className="flex flex-col gap-1 text-sm" htmlFor="advisor-mode">
        <span className="text-muted-foreground">Mode</span>
        <select
          id="advisor-mode"
          value={config.mode}
          onChange={(event) =>
            setConfig((current) => ({ ...current, mode: event.target.value as AdvisorMode }))
          }
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        >
          {MODES.map((mode) => (
            <option key={mode} value={mode}>{mode}</option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-sm" htmlFor="advisor-timeout">
        <span className="text-muted-foreground">Timeout ms</span>
        <input
          id="advisor-timeout"
          type="number"
          min="100"
          max="10000"
          value={config.timeout_ms}
          onChange={(event) => setNumber('timeout_ms', event.target.value)}
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        />
      </label>

      <label className="flex flex-col gap-1 text-sm" htmlFor="advisor-budget">
        <span className="text-muted-foreground">Daily budget USD</span>
        <input
          id="advisor-budget"
          type="text"
          value={config.daily_budget_usd}
          onChange={(event) =>
            setConfig((current) => ({ ...current, daily_budget_usd: event.target.value }))
          }
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        />
      </label>

      <label className="flex flex-col gap-1 text-sm" htmlFor="advisor-auto-pause">
        <span className="text-muted-foreground">Auto pause threshold</span>
        <input
          id="advisor-auto-pause"
          type="number"
          min="0"
          value={config.auto_pause_threshold}
          onChange={(event) => setNumber('auto_pause_threshold', event.target.value)}
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        />
      </label>

      <label className="flex flex-col gap-1 text-sm" htmlFor="advisor-min-confidence">
        <span className="text-muted-foreground">Min veto confidence</span>
        <input
          id="advisor-min-confidence"
          type="number"
          min="0"
          max="1"
          step="0.01"
          value={config.min_veto_confidence}
          onChange={(event) => setNumber('min_veto_confidence', event.target.value)}
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        />
      </label>

      <button type="submit" disabled={mutation.isPending} className="btn-primary">
        {mutation.isPending ? 'Saving…' : saved ? 'Saved' : 'Save advisor config'}
      </button>

      {(validationError != null || mutation.isError) && (
        <p role="alert" className="text-xs text-destructive">
          {validationError ?? 'Failed to save advisor config.'}
        </p>
      )}
    </form>
  );
}

export function AdvisorConfigForm({ botId }: Props): React.JSX.Element {
  const query = useQuery({
    queryKey: ['bot', botId, 'advisor-config'],
    queryFn: () => getAdvisorConfig(botId),
  });

  if (query.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (query.isError) {
    return <p role="alert" className="text-sm text-destructive">Failed to load advisor config.</p>;
  }

  const initialConfig = query.data?.config ?? defaultConfig();
  return (
    <AdvisorConfigFormInner
      key={query.data != null ? 'loaded' : 'init'}
      botId={botId}
      initialConfig={initialConfig}
    />
  );
}
