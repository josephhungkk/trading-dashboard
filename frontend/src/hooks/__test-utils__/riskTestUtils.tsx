/**
 * Shared Vitest helpers for risk-surface hook tests.
 *
 * Extracted from the duplicated makeWrapper / jsonResponse in
 * useRiskLimits.test.tsx + useAccountKillSwitch.test.tsx during the
 * Chunk E reviewer-fix pass (E7).
 */

import * as React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

interface WrapperProps {
  children: React.ReactNode;
}

export function makeWrapper(client: QueryClient): React.FC<WrapperProps> {
  return function HookWrapper(props: WrapperProps) {
    return <QueryClientProvider client={client}>{props.children}</QueryClientProvider>;
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function noRetryQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}
