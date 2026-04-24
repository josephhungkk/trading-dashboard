import { createRootRoute } from '@tanstack/react-router';
import * as React from 'react';
import { AppShell } from '@/components/layout/AppShell';

function RootErrorBoundary({ error }: { error: Error }): React.JSX.Element {
  return (
    <div role="alert" style={{ padding: '2rem' }}>
      <h1>Something went wrong</h1>
      <pre>{error.message}</pre>
      <button type="button" onClick={() => location.reload()}>Reload</button>
    </div>
  );
}

export const Route = createRootRoute({
  component: AppShell,
  errorComponent: RootErrorBoundary,
});
