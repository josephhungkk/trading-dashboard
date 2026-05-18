import { createFileRoute } from '@tanstack/react-router';

import { FundsPage } from '@/features/funds/FundsPage';

export const Route = createFileRoute('/funds')({
  component: FundsPage,
});
