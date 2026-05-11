import { createFileRoute } from '@tanstack/react-router';
import { RiskLimitsPage } from '@/features/admin/risk/RiskLimitsPage';

export const Route = createFileRoute('/admin/risk')({ component: RiskLimitsPage });
