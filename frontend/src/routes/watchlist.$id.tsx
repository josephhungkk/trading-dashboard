import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/watchlist/$id')({
  component: WatchlistId,
});

function WatchlistId() {
  const { id } = Route.useParams();
  return (
    <div style={{ padding: '2rem' }}>
      <h2>Watchlist {id} (stub — Task 39)</h2>
    </div>
  );
}
