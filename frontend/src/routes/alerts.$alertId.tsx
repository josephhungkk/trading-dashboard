import { createFileRoute } from '@tanstack/react-router';

import { AlertDetailPage } from '@/features/alerts/AlertDetailPage';

export const Route = createFileRoute('/alerts/$alertId')({
  component: AlertDetailPage,
});
