import { createFileRoute } from '@tanstack/react-router';
import { AdminAiPage } from '@/features/admin/ai/AdminAiPage';

export const Route = createFileRoute('/admin/ai')({
  component: AdminAiPage,
});
