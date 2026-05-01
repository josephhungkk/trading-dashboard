import * as React from 'react';
import { RadioGroup, RadioItem } from '@/components/primitives/Radio';
import { Switch } from '@/components/primitives/Switch';
import { SchwabCard } from './SchwabCard';

const DENSITY_STORAGE_KEY = 'dashboard.settings.density';
const SOUND_STORAGE_KEY = 'dashboard.settings.sound';

type Density = 'comfortable' | 'compact';

type HealthState =
  | { status: 'loading' }
  | { status: 'ready'; data: unknown }
  | { status: 'error'; message: string };

const DENSITY_OPTIONS: readonly Density[] = ['comfortable', 'compact'];

export function SettingsPage(): React.JSX.Element {
  const [density, setDensity] = React.useState<Density>(() => readDensity());
  const [soundEnabled, setSoundEnabled] = React.useState<boolean>(() => readStoredBoolean(SOUND_STORAGE_KEY, false));
  const [health, setHealth] = React.useState<HealthState>({ status: 'loading' });

  React.useEffect(() => {
    const controller = new AbortController();

    async function loadHealth(signal: AbortSignal): Promise<void> {
      setHealth({ status: 'loading' });
      try {
        const response = await fetch('/health', { signal });
        if (!response.ok) throw new Error(`health ${response.status}`);
        const data: unknown = await response.json();
        setHealth({ status: 'ready', data });
      } catch (err) {
        if (signal.aborted) return;
        setHealth({ status: 'error', message: messageFrom(err) });
      }
    }

    loadHealth(controller.signal).catch((err: unknown) => {
      if (!controller.signal.aborted) {
        setHealth({ status: 'error', message: messageFrom(err) });
      }
    });

    return () => {
      controller.abort();
    };
  }, []);

  function handleDensityChange(value: string): void {
    if (!isDensity(value)) return;
    setDensity(value);
    writeStorage(DENSITY_STORAGE_KEY, value);
  }

  function handleSoundChange(checked: boolean): void {
    setSoundEnabled(checked);
    writeStorage(SOUND_STORAGE_KEY, String(checked));
  }

  const viteEnv = getViteEnvEntries();
  const buildSha = (import.meta.env.VITE_BUILD_SHA as string | undefined) ?? null;

  return (
    <section className="flex h-full min-h-0 flex-col gap-4 p-4" aria-label="Settings">
      <header className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-fg">Settings</h2>
      </header>

      <form className="grid gap-4" aria-label="Dashboard settings">
        <section className="grid gap-3 rounded-lg border border-border bg-panel p-4" aria-labelledby="settings-theme">
          <div className="flex items-start justify-between gap-4">
            <div className="grid gap-1">
              <label htmlFor="settings-theme-toggle" id="settings-theme" className="text-sm font-medium text-fg">
                Theme
              </label>
              <p id="settings-theme-help" className="text-sm text-fg-muted">
                coming soon — Phase 3.5
              </p>
            </div>
            <Switch
              id="settings-theme-toggle"
              checked={false}
              disabled
              aria-describedby="settings-theme-help"
            />
          </div>
        </section>

        <section className="grid gap-3 rounded-lg border border-border bg-panel p-4" aria-labelledby="settings-density">
          <fieldset className="grid gap-3">
            <legend id="settings-density" className="text-sm font-medium text-fg">
              Density
            </legend>
            <RadioGroup value={density} onValueChange={handleDensityChange} aria-labelledby="settings-density">
              {DENSITY_OPTIONS.map((option) => {
                const id = `settings-density-${option}`;
                return (
                  <div key={option} className="flex items-center gap-2">
                    <RadioItem id={id} value={option} />
                    <label htmlFor={id} className="text-sm capitalize text-fg">
                      {option}
                    </label>
                  </div>
                );
              })}
            </RadioGroup>
          </fieldset>
        </section>

        <section className="grid gap-3 rounded-lg border border-border bg-panel p-4" aria-labelledby="settings-sound">
          <div className="flex items-start justify-between gap-4">
            <div className="grid gap-1">
              <label htmlFor="settings-sound-toggle" id="settings-sound" className="text-sm font-medium text-fg">
                Sound
              </label>
              <p id="settings-sound-help" className="text-sm text-fg-muted">
                Stores a preference only.
              </p>
            </div>
            <Switch
              id="settings-sound-toggle"
              checked={soundEnabled}
              onCheckedChange={handleSoundChange}
              aria-describedby="settings-sound-help"
            />
          </div>
        </section>
      </form>

      <section>
        <h2>Brokers</h2>
        <SchwabCard />
      </section>

      <section className="grid min-h-0 gap-3 rounded-lg border border-border bg-panel p-4" aria-labelledby="settings-about">
        <h3 id="settings-about" className="text-base font-semibold text-fg">
          About
        </h3>
        <div className="grid gap-2">
          <h4 className="text-sm font-medium text-fg">Backend health</h4>
          {health.status === 'loading' && <p className="text-sm text-fg-muted">Loading health</p>}
          {health.status === 'error' && <p className="text-sm text-negative">{health.message}</p>}
          {health.status === 'ready' && (
            <pre className="max-h-64 overflow-auto rounded-md border border-border bg-bg p-3 text-xs text-fg">
              {formatJson(health.data)}
            </pre>
          )}
        </div>

        <div className="grid gap-2">
          <h4 className="text-sm font-medium text-fg">Vite environment</h4>
          <dl className="grid gap-2 text-sm">
            <div className="grid gap-1">
              <dt className="font-medium text-fg">VITE_BUILD_SHA</dt>
              <dd className="font-mono text-fg-muted">{buildSha ?? 'null'}</dd>
            </div>
            {viteEnv.map(([key, value]) => (
              <div key={key} className="grid gap-1">
                <dt className="font-medium text-fg">{key}</dt>
                <dd className="font-mono text-fg-muted">{formatEnvValue(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>
    </section>
  );
}

function readDensity(): Density {
  const stored = readStorage(DENSITY_STORAGE_KEY);
  return isDensity(stored) ? stored : 'comfortable';
}

function readStoredBoolean(key: string, fallback: boolean): boolean {
  const stored = readStorage(key);
  if (stored === 'true') return true;
  if (stored === 'false') return false;
  return fallback;
}

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    return;
  }
}

function isDensity(value: string | null): value is Density {
  return value === 'comfortable' || value === 'compact';
}

function getViteEnvEntries(): [string, unknown][] {
  return Object.entries(import.meta.env).filter(([key]) => key.startsWith('VITE_'));
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function formatEnvValue(value: unknown): string {
  if (value === undefined) return 'undefined';
  if (value === null) return 'null';
  return String(value);
}

function messageFrom(err: unknown): string {
  return err instanceof Error ? err.message : 'Health request failed';
}
