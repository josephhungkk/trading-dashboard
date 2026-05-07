import * as React from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { ChartPage } from '@/features/chart/ChartPage';

export const Route = createFileRoute('/chart/$canonicalId')({
  component: ChartRoute,
});

function ChartRoute(): React.JSX.Element {
  const { canonicalId } = Route.useParams();
  return <ChartPage canonicalId={canonicalId} />;
}
