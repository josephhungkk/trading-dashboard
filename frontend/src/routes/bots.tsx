import { createFileRoute } from '@tanstack/react-router';
import { BotsPage } from '../features/bots/BotsPage';

export const Route = createFileRoute('/bots')({
  component: BotsPage,
});
