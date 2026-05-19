import { createFileRoute } from '@tanstack/react-router';
import { BotCreatePage } from '../features/bots/BotCreatePage';

export const Route = createFileRoute('/bots/new')({
  component: BotCreatePage,
});
