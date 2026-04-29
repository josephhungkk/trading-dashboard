import * as React from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { FillsTable } from '@/components/patterns/FillsTable';
import { useFillsHistory } from '@/hooks/useFillsHistory';
import { fetchOrder } from '@/services/api';

export const Route = createFileRoute('/orders/$id/fills')({
  component: OrderFillsPage,
});

function OrderFillsPage(): React.JSX.Element {
  const { id } = Route.useParams();
  const [accountId, setAccountId] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    fetchOrder(id).then(order => {
      if (!cancelled) setAccountId(order.account_id);
    }).catch(() => {
      // error surfaced by a parent error boundary
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const { from, to } = React.useMemo(() => {
    const now = new Date();
    const fromDate = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString();
    return { from: fromDate, to: now.toISOString() };
  }, []);

  const { fills, loadMore, hasMore, isLoading } = useFillsHistory(
    accountId !== null ? { accountId, from, to } : { accountId: '', from, to },
  );

  if (accountId === null) return <div>Loading…</div>;

  return (
    <section aria-label={`Fills for order ${id}`}>
      <h1 className="text-lg font-semibold mb-4">Fills</h1>
      <FillsTable
        fills={fills}
        onLoadMore={loadMore}
        hasMore={hasMore}
        isLoading={isLoading}
      />
    </section>
  );
}
