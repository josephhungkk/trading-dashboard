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
// eslint-disable-next-line boundaries/element-types -- account picker reads active-scope accounts via registry (h1 allowed via factory)
import { useActiveStores } from '@/stores/registry';
// eslint-disable-next-line boundaries/element-types -- broker label metadata is static lookup
import { BROKERS } from '@/services/fixtures';

export function AccountPicker(): React.JSX.Element {
  const { useAccounts } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const selectedAccountId = useAccounts((s) => s.selectedAccountId);
  const select = useAccounts((s) => s.select);
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
                <DropdownMenuItem
                  key={a.id}
                  onSelect={() => select(a.id)}
                  className="flex items-center justify-between gap-2"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <Avatar className="h-6 w-6">
                      <AvatarFallback>{initials(a.alias)}</AvatarFallback>
                    </Avatar>
                    <span className="truncate">{a.alias}</span>
                  </span>
                  <NumericCell value={a.nlv} format="currency" currency={a.baseCurrency} />
                </DropdownMenuItem>
              ))}
            </React.Fragment>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
