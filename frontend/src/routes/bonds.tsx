import { createFileRoute } from '@tanstack/react-router';

import { BondsPage } from '@/features/bonds/BondsPage';

export const Route = createFileRoute('/bonds')({
  component: BondsPage,
});
