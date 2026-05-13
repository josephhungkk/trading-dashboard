import { useMutation } from '@tanstack/react-query';

import { adminFetch, mintCsrfNonce } from '@/services/admin/api';

export interface DryRunSampleFire {
  ts: number | string;
  close?: number;
  [key: string]: unknown;
}

export interface DryRunResult {
  replay_resolution: '1m' | '1d' | 'insufficient';
  fire_count: number;
  sample_fires: DryRunSampleFire[];
  truncated: boolean;
}

export function useDryRun(): ReturnType<
  typeof useMutation<DryRunResult, Error, Record<string, unknown>>
> {
  return useMutation<DryRunResult, Error, Record<string, unknown>>({
    mutationFn: async (predicate_json: Record<string, unknown>) => {
      const nonce = await mintCsrfNonce();
      return adminFetch<DryRunResult>('/api/alerts/dry-run', {
        method: 'POST',
        body: JSON.stringify({ predicate_json }),
        headers: { 'X-CSRF-Nonce': nonce },
      });
    },
  });
}
