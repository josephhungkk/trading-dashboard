import { memo } from 'react';
import * as React from 'react';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/primitives/DropdownMenu';
import { Avatar, AvatarFallback, initials } from '@/components/primitives/Avatar';
import { NumericCell } from '@/components/primitives/NumericCell';
import { Button } from '@/components/primitives/Button';
import { cn } from '@/lib/utils';
// eslint-disable-next-line boundaries/element-types -- account picker row props use account shape from service contract
import type { Account } from '@/services/types';
// eslint-disable-next-line boundaries/element-types -- account picker reads active-scope accounts via registry (h1 allowed via factory)
import { useActiveStores } from '@/stores/registry';
// eslint-disable-next-line boundaries/element-types -- account picker reads fleet maintenance state for per-row stale NLV display
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';
// eslint-disable-next-line boundaries/element-types -- account picker row props use maintenance shape from global store contract
import type { FleetMaintenance } from '@/stores/global/fleet-maintenance';
// eslint-disable-next-line boundaries/element-types -- broker label metadata is static lookup
import { BROKERS } from '@/services/fixtures';
import { nlvCellState } from './nlv-cell-state';

interface AccountRowProps {
  account: Account;
  maintenance: FleetMaintenance;
  onSelect: (accountId: string) => void;
  renderAccountAction?: AccountActionRenderer | undefined;
}

interface AccountPickerProps {
  renderAccountAction?: AccountActionRenderer | undefined;
}

export interface AccountActionArgs {
  account: Account;
  maintenance: FleetMaintenance;
}

type AccountActionRenderer = (args: AccountActionArgs) => React.ReactNode;

function formatCurrency(value: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(value);
}

function AccountRowComponent({
  account,
  maintenance,
  onSelect,
  renderAccountAction,
}: AccountRowProps): React.JSX.Element {
  const cell = nlvCellState(account, maintenance);
  const action = renderAccountAction?.({ account, maintenance });

  return (
    <DropdownMenuItem
      onSelect={() => onSelect(account.id)}
      className="flex items-center justify-between gap-2"
    >
      <span className="flex min-w-0 items-center gap-2">
        <Avatar className="h-6 w-6">
          <AvatarFallback>{initials(account.alias)}</AvatarFallback>
        </Avatar>
        <span className="truncate">{account.alias}</span>
      </span>
      <span
        className={cn(
          'font-mono tabular-nums text-right inline-block text-fg',
          cell.variant === 'dim' && 'opacity-60',
          cell.variant === 'placeholder' && 'text-muted-foreground',
        )}
        title={cell.tooltip ?? undefined}
      >
        {cell.variant === 'placeholder'
          ? cell.value
          : formatCurrency(cell.value, account.baseCurrency)}
      </span>
      {action ? <span onPointerDown={(event) => event.stopPropagation()}>{action}</span> : null}
    </DropdownMenuItem>
  );
}

const AccountRow = memo(
  AccountRowComponent,
  (prev, next) =>
    prev.account.id === next.account.id &&
    prev.account.nlv === next.account.nlv &&
    prev.account.nlvAt?.getTime() === next.account.nlvAt?.getTime() &&
    prev.maintenance.active === next.maintenance.active &&
    prev.maintenance.window === next.maintenance.window &&
    prev.maintenance.until?.getTime() === next.maintenance.until?.getTime() &&
    prev.renderAccountAction === next.renderAccountAction,
);

export function AccountPicker({
  renderAccountAction,
}: AccountPickerProps = {}): React.JSX.Element {
  const { useAccounts } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const selectedAccountId = useAccounts((s) => s.selectedAccountId);
  const select = useAccounts((s) => s.select);
  const maintenance = useFleetMaintenance((s) => s.maintenance);
  const selected = accounts.find((a) => a.id === selectedAccountId);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="min-w-56 justify-between gap-3">
          <span className="flex min-w-0 items-center gap-2">
            <Avatar className="h-7 w-7">
              <AvatarFallback>{selected ? initials(selected.alias) : '—'}</AvatarFallback>
            </Avatar>
            <span className="truncate">{selected?.alias ?? 'Select account'}</span>
          </span>
          {selected && (
            <NumericCell
              value={selected.nlv}
              format="currency"
              currency={selected.baseCurrency}
            />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        {BROKERS.map((b, i) => {
          const rows = accounts.filter((a) => a.broker === b.id);
          if (rows.length === 0) return null;
          return (
            <React.Fragment key={b.id}>
              {i > 0 && <DropdownMenuSeparator />}
              <DropdownMenuLabel>{b.name}</DropdownMenuLabel>
              {rows.map((a) => (
                <AccountRow
                  key={a.id}
                  account={a}
                  maintenance={maintenance}
                  onSelect={select}
                  renderAccountAction={renderAccountAction}
                />
              ))}
            </React.Fragment>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
