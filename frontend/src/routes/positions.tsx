import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/positions')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Positions (stub — Task 38)</h2>
    </div>
  ),
});
