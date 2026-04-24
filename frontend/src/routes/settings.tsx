import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/settings')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Settings (stub — Task 41)</h2>
    </div>
  ),
});
