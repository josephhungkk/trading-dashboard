import * as React from 'react';
import { useTradeTicket } from '@/features/orders/use-trade-ticket';

interface Props {
  accountId: string;
  conid?: string;
  symbol?: string;
  tradeEnabled: boolean;
  maintenanceActive: boolean;
}

export function TradeButton({
  accountId,
  conid,
  symbol,
  tradeEnabled,
  maintenanceActive,
}: Props): React.JSX.Element {
  const { open } = useTradeTicket();
  const disabled = !tradeEnabled || maintenanceActive;
  const openTicket = React.useCallback(() => {
    open({ accountId, conid, symbol });
  }, [accountId, conid, open, symbol]);
  const tooltip = !tradeEnabled
    ? 'Trading not enabled for this account'
    : maintenanceActive
      ? 'Broker maintenance window — try again later'
      : undefined;
  return (
    <button
      type="button"
      disabled={disabled}
      title={tooltip}
      onPointerDown={(event) => {
        event.preventDefault();
        event.stopPropagation();
        openTicket();
      }}
      onClick={(event) => {
        event.stopPropagation();
        if (event.detail === 0) openTicket();
      }}
    >
      Trade
    </button>
  );
}
