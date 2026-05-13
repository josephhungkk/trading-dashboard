import { createFileRoute } from '@tanstack/react-router';

import { ChatPage } from '@/features/ai/ChatPage';

export const Route = createFileRoute('/ai/chat')({
  component: ChatPage,
});
