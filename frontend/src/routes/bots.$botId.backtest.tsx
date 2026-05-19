import { createFileRoute } from '@tanstack/react-router';
import { BacktestPage } from '../features/bots/pages/BacktestPage';

export const Route = createFileRoute('/bots/$botId/backtest')({
  component: BacktestPage,
});
