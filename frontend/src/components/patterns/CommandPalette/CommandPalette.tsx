import * as React from 'react';
import { Command } from 'cmdk';
import { useNavigate } from '@tanstack/react-router';
// eslint-disable-next-line boundaries/element-types -- global commands store drives the palette
import { useCommandsStore } from '@/stores/global/commands';
// eslint-disable-next-line boundaries/element-types -- fixtures for symbol + account lookup (mock layer)
import { SYMBOLS, STRESS_SYMBOLS } from '@/services/fixtures';
// eslint-disable-next-line boundaries/element-types -- active-scope accounts for @ prefix routing
import { useActiveStores } from '@/stores/registry';
import { cn } from '@/lib/utils';

type Prefix = '' | '>' | '@' | '/' | '?';

interface RouteEntry { readonly path: string; readonly label: string }
interface ShortcutEntry { readonly keys: string; readonly label: string }

const ROUTES: readonly RouteEntry[] = [
  { path: '/overview',  label: 'Overview' },
  { path: '/orders',    label: 'Orders' },
  { path: '/positions', label: 'Positions' },
  { path: '/watchlist', label: 'Watchlist' },
  { path: '/admin',     label: 'Admin' },
  { path: '/settings',  label: 'Settings' },
];

const SHORTCUTS: readonly ShortcutEntry[] = [
  { keys: 'Cmd+K',         label: 'Open command palette' },
  { keys: 'Cmd+[ / Cmd+]', label: 'Collapse left / right panel' },
  { keys: 'Cmd+Shift+M',   label: 'Toggle live / paper mode' },
  { keys: 'Cmd+1..6',      label: 'Jump to route 1..6' },
  { keys: 'Esc',           label: 'Close palette / dialog / drawer' },
  { keys: '/',             label: 'Focus search on current page' },
  { keys: '?',             label: 'Open palette with ? prefix' },
];

function detectPrefix(input: string): { prefix: Prefix; rest: string } {
  if (input.startsWith('>')) return { prefix: '>', rest: input.slice(1).trim() };
  if (input.startsWith('@')) return { prefix: '@', rest: input.slice(1).trim() };
  if (input.startsWith('/')) return { prefix: '/', rest: input.slice(1).trim() };
  if (input.startsWith('?')) return { prefix: '?', rest: input.slice(1).trim() };
  return { prefix: '', rest: input };
}

export function CommandPalette(): React.JSX.Element {
  const open = useCommandsStore((s) => s.open);
  const setOpen = useCommandsStore((s) => s.setOpen);
  const commands = useCommandsStore((s) => s.commands);
  const { useAccounts } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const selectAccount = useAccounts((s) => s.select);
  const navigate = useNavigate();

  const [value, setValue] = React.useState('');

  // Global Cmd+K / Ctrl+K listener. State toggle lives inside the event handler
  // (not in the effect body) so react-hooks/set-state-in-effect does not fire.
  React.useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        const { open: isOpen, setOpen: toggle } = useCommandsStore.getState();
        toggle(!isOpen);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); };
  }, []);

  // Clear the input inside the open-change callback — not in an effect — to avoid
  // the set-state-in-effect lint rule when the palette re-opens.
  function handleOpenChange(next: boolean): void {
    if (next) setValue('');
    setOpen(next);
  }

  const { prefix } = detectPrefix(value);

  function runAndClose(fn: () => void | Promise<void>): void {
    void fn();
    setOpen(false);
  }

  const symbolsToShow = React.useMemo(
    () => [...SYMBOLS, ...STRESS_SYMBOLS].slice(0, 50),
    [],
  );

  return (
    <Command.Dialog
      open={open}
      onOpenChange={handleOpenChange}
      label="Command palette"
      className={cn(
        'fixed left-1/2 top-1/4 z-50 w-full max-w-xl -translate-x-1/2',
        'rounded-lg border border-border bg-panel text-fg shadow-xl',
      )}
    >
      <Command.Input
        value={value}
        onValueChange={setValue}
        placeholder="Type to search, or > @ / ? for a prefix…"
        className={cn(
          'w-full border-b border-border bg-transparent px-4 py-3 text-sm text-fg outline-none',
          'placeholder:text-fg-subtle',
        )}
      />
      <Command.List className="max-h-96 overflow-y-auto p-2">
        <Command.Empty className="px-2 py-3 text-sm text-fg-muted">No results.</Command.Empty>

        {prefix === '' && (
          <Command.Group heading="Symbols" className="text-xs text-fg-muted">
            {symbolsToShow.map((s) => (
              <Command.Item
                key={s.symbol}
                value={`${s.symbol} ${s.description}`}
                onSelect={() => runAndClose(() => { /* noop: symbol detail route lands in a later phase */ })}
                className="flex cursor-pointer items-center justify-between rounded px-2 py-1 text-sm text-fg data-[selected=true]:bg-accent-active data-[selected=true]:text-primary-fg"
              >
                <span className="font-medium">{s.symbol}</span>
                <span className="text-xs text-fg-muted">{s.description}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {prefix === '>' && (
          <Command.Group heading="Commands">
            {commands.map((c) => (
              <Command.Item
                key={c.id}
                value={`${c.label} ${(c.keywords ?? []).join(' ')}`}
                onSelect={() => runAndClose(c.run)}
                className="flex cursor-pointer items-center rounded px-2 py-1 text-sm text-fg data-[selected=true]:bg-accent-active data-[selected=true]:text-primary-fg"
              >
                {c.label}
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {prefix === '@' && (
          <Command.Group heading="Accounts">
            {accounts.map((a) => (
              <Command.Item
                key={a.id}
                value={`${a.alias} ${a.accountNumber}`}
                onSelect={() => runAndClose(() => selectAccount(a.id))}
                className="flex cursor-pointer items-center justify-between rounded px-2 py-1 text-sm text-fg data-[selected=true]:bg-accent-active data-[selected=true]:text-primary-fg"
              >
                <span>{a.alias}</span>
                <span className="text-xs text-fg-muted">{a.accountNumber}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {prefix === '/' && (
          <Command.Group heading="Routes">
            {ROUTES.map((r) => (
              <Command.Item
                key={r.path}
                value={`${r.path} ${r.label}`}
                onSelect={() => runAndClose(() => { void navigate({ to: r.path }); })}
                className="flex cursor-pointer items-center justify-between rounded px-2 py-1 text-sm text-fg data-[selected=true]:bg-accent-active data-[selected=true]:text-primary-fg"
              >
                <span>{r.label}</span>
                <span className="text-xs text-fg-muted">{r.path}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {prefix === '?' && (
          <Command.Group heading="Shortcuts">
            {SHORTCUTS.map((s) => (
              <Command.Item
                key={s.keys}
                value={`${s.keys} ${s.label}`}
                onSelect={() => runAndClose(() => { /* noop: shortcuts are informational */ })}
                className="flex cursor-pointer items-center justify-between rounded px-2 py-1 text-sm text-fg data-[selected=true]:bg-accent-active data-[selected=true]:text-primary-fg"
              >
                <span>{s.label}</span>
                <kbd className="rounded border border-border bg-elevated px-1.5 py-0.5 text-xs text-fg-muted">{s.keys}</kbd>
              </Command.Item>
            ))}
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}
