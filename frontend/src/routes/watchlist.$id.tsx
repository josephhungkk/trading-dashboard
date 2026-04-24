import * as React from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { WatchlistPage } from '@/features/watchlist/WatchlistPage';
 
import { useActiveStores } from '@/stores/registry';
export const Route = createFileRoute('/watchlist/$id')({ component: WatchlistRoute });
function WatchlistRoute(): React.JSX.Element {
  const { id } = Route.useParams();
  const stores = useActiveStores();
  React.useEffect(() => {
    stores.useWatchlists.getState().setActive(id);
  }, [id, stores]);
  return <WatchlistPage />;
}
