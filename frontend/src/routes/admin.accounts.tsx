import { createFileRoute } from '@tanstack/react-router';
import { AdminAccountsPage } from '@/features/admin/accounts/AdminAccountsPage';

export const Route = createFileRoute('/admin/accounts')({ component: AdminAccountsPage });
