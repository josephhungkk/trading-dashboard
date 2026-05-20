import { createFileRoute } from '@tanstack/react-router';
import { OrchestrationPage } from '../features/orchestration/OrchestrationPage';

interface OrchestrationSearch {
  account_id?: string;
}

function validateSearch(search: Record<string, unknown>): OrchestrationSearch {
  const result: OrchestrationSearch = {};
  if (typeof search.account_id === 'string') result.account_id = search.account_id;
  return result;
}

export const Route = createFileRoute('/orchestration')({
  component: OrchestrationPage,
  validateSearch,
});
