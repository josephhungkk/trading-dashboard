import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/overview')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Overview (stub — Task 37)</h2>
    </div>
  ),
});
