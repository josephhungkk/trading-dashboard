import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/more')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>More (stub — Task 32)</h2>
    </div>
  ),
});
