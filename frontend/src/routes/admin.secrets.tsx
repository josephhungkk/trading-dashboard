import { createFileRoute } from '@tanstack/react-router';
import { AdminSecretsPage } from '@/features/admin/AdminSecretsPage';

export const Route = createFileRoute('/admin/secrets')({ component: AdminSecretsPage });
