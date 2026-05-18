import { createFileRoute } from '@tanstack/react-router';
import { ForexPage } from '@/features/forex/ForexPage';

export const Route = createFileRoute('/forex')({ component: ForexPage });
