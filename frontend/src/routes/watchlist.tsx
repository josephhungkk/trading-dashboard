import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/watchlist')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Watchlist (stub — Task 39)</h2>
    </div>
  ),
});
