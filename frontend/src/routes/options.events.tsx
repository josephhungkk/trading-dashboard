import { createFileRoute } from '@tanstack/react-router';

import { OptionEventsPage } from '@/features/options/OptionEventsPage';

export const Route = createFileRoute('/options/events')({
  component: OptionEventsPage,
});
