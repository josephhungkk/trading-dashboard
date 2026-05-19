import { createFileRoute } from '@tanstack/react-router';
import { BotsPage } from '../features/bots/BotsPage';

interface BotsSearch {
  status?: string;
}

function validateSearch(search: Record<string, unknown>): BotsSearch {
  const result: BotsSearch = {};
  if (typeof search.status === 'string') result.status = search.status;
  return result;
}

export const Route = createFileRoute('/bots')({
  component: BotsPage,
  validateSearch,
});
