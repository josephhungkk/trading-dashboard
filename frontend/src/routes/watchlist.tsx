import { createFileRoute } from '@tanstack/react-router';
import { WatchlistPage } from '@/features/watchlist/WatchlistPage';
export const Route = createFileRoute('/watchlist')({ component: WatchlistPage });
