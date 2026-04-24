import { createFileRoute } from '@tanstack/react-router';
import { AlertsStubPage } from '@/features/alerts/AlertsStubPage';

export const Route = createFileRoute('/alerts')({ component: AlertsStubPage });
