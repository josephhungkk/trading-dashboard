import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/admin')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Admin (stub — Task 40)</h2>
    </div>
  ),
});
