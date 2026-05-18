import { createFileRoute } from '@tanstack/react-router';

import { FuturesPage } from '@/features/futures/FuturesPage';

export const Route = createFileRoute('/futures')({
  component: FuturesPage,
});
