import { createFileRoute } from '@tanstack/react-router';
import { AdvisorFeedPage } from '../features/bots/pages/AdvisorFeedPage';

export const Route = createFileRoute('/admin/bots/advisor-feed')({
  component: AdvisorFeedPage,
});
