import * as React from 'react';
import {
  AccountPicker as PatternAccountPicker,
  type AccountActionArgs,
} from '@/components/patterns/AccountPicker/AccountPicker';
import { useActiveStores } from '@/stores/registry';
import { TradeButton } from '@/features/orders/TradeButton';
import { TradeTicketModal } from '@/features/orders/TradeTicketModal';

interface PolicyResponse {
  trade_enabled: boolean;
}

function isPolicyResponse(value: unknown): value is PolicyResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'trade_enabled' in value &&
    typeof value.trade_enabled === 'boolean'
  );
}

async function fetchTradeEnabled(accountId: string, signal: AbortSignal): Promise<boolean> {
  const response = await fetch(`/api/orders/policy/${encodeURIComponent(accountId)}`, {
    credentials: 'include',
    signal,
  });
  if (!response.ok) return false;
  const body: unknown = await response.json();
  return isPolicyResponse(body) ? body.trade_enabled : false;
}

export function AccountPicker(): React.JSX.Element {
  const { useAccounts } = useActiveStores();
  const accounts = useAccounts((s) => s.accounts);
  const [tradePolicies, setTradePolicies] = React.useState<Record<string, boolean>>({});

  React.useEffect(() => {
    const controller = new AbortController();
    const accountIds = accounts.map((account) => account.id);
    void Promise.all(
      accountIds.map(async (accountId) => [accountId, await fetchTradeEnabled(accountId, controller.signal)] as const),
    )
      .then((entries) => {
        if (controller.signal.aborted) return;
        setTradePolicies(Object.fromEntries(entries));
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setTradePolicies(Object.fromEntries(accountIds.map((accountId) => [accountId, false])));
      });
    return () => controller.abort();
  }, [accounts]);

  const renderAccountAction = React.useCallback(
    ({ account, maintenance }: AccountActionArgs) => (
      <TradeButton
        accountId={account.id}
        tradeEnabled={tradePolicies[account.id] ?? false}
        maintenanceActive={maintenance.active}
      />
    ),
    [tradePolicies],
  );

  return (
    <>
      <PatternAccountPicker renderAccountAction={renderAccountAction} />
      <TradeTicketModal />
    </>
  );
}
