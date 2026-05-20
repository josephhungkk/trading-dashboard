import { createFileRoute } from '@tanstack/react-router';
import { TaxPage } from '@/features/tax/pages/TaxPage';

export const Route = createFileRoute('/tax')({ component: TaxPage });
