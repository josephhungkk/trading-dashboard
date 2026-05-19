import { createFileRoute } from '@tanstack/react-router';
import { BotDetailPage } from '../features/bots/BotDetailPage';

export const Route = createFileRoute('/bots/$botId')({
  component: BotDetailPage,
});
