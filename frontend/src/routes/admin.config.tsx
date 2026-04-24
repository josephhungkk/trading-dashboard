import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/admin/config')({
  component: () => (
    <div style={{ padding: '2rem' }}>
      <h2>Admin Config (stub — Task 40)</h2>
    </div>
  ),
});
