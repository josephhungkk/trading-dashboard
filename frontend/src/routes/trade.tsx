import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/trade')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Trade (stub — Task 42)</h2>
    </div>
  ),
});
