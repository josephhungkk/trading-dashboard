import { createFileRoute } from '@tanstack/react-router';
import { AdminTelegramPage } from '@/features/admin/telegram/AdminTelegramPage';

export const Route = createFileRoute('/admin/telegram')({
  component: AdminTelegramPage,
});
