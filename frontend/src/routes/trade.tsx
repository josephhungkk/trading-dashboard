import { createFileRoute } from '@tanstack/react-router';
import { TradeStubPage } from '@/features/trade/TradeStubPage';

export const Route = createFileRoute('/trade')({ component: TradeStubPage });
