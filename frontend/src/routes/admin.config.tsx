import { createFileRoute } from '@tanstack/react-router';
import { AdminConfigPage } from '@/features/admin/AdminConfigPage';

export const Route = createFileRoute('/admin/config')({ component: AdminConfigPage });
