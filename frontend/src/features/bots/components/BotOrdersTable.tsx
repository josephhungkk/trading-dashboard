import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { listBotOrders } from '../../../services/bots/api';

interface Props {
  botId: string;
}

export function BotOrdersTable({ botId }: Props): React.JSX.Element {
  const { data, isLoading } = useQuery({
    queryKey: ['bot-orders', botId],
    queryFn: () => listBotOrders(botId),
  });

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading orders…</p>;

  const items = data?.items ?? [];

  if (items.length === 0)
    return <p className="text-sm text-muted-foreground">No orders yet.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="pb-2 pr-4">Placed</th>
            <th className="pb-2 pr-4">Side</th>
            <th className="pb-2 pr-4">Qty</th>
            <th className="pb-2 pr-4">Status</th>
            <th className="pb-2">Account</th>
          </tr>
        </thead>
        <tbody>
          {items.map((order) => (
            <tr key={order.order_id} className="border-b last:border-0">
              <td className="py-2 pr-4 font-mono text-xs">
                {new Date(order.placed_at).toLocaleString()}
              </td>
              <td
                className={`py-2 pr-4 font-medium ${
                  order.side.toLowerCase() === 'buy' ? 'text-green-700' : 'text-red-700'
                }`}
              >
                {order.side.toUpperCase()}
              </td>
              <td className="py-2 pr-4 font-mono">{order.qty}</td>
              <td className="py-2 pr-4 text-muted-foreground">{order.status}</td>
              <td className="py-2 font-mono text-xs">{order.account_id.slice(0, 8)}…</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
