import { createFileRoute } from '@tanstack/react-router';
import { RiskDecisionsPage } from '@/features/admin/risk/RiskDecisionsPage';

export const Route = createFileRoute('/admin/risk/decisions')({ component: RiskDecisionsPage });
