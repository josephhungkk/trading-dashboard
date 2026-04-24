import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/alerts')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Alerts (stub — Task 42)</h2>
    </div>
  ),
});
