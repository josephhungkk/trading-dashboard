import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/orders')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Orders (stub — Task 38)</h2>
    </div>
  ),
});
