import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/admin/secrets')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Admin Secrets (stub — Task 40)</h2>
    </div>
  ),
});
