/**
 * Phase 10a E1 — TanStack Query hook over /api/risk/limits +
 * /api/admin/risk-limits CRUD.
 *
 * Spec §7 [M9]: every mutation's `onSuccess` must
 * queryClient.invalidateQueries(['risk-limits']) so the FE cache drops
 * the moment the admin write returns. The backend Redis pubsub only
 * busts server-side caches; the FE TanStack cache is independent.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import {
  createRiskLimit,
  deleteRiskLimit,
  listRiskLimits,
  updateRiskLimit,
} from '@/services/risk/api';
import type {
  RiskLimitCreate,
  RiskLimitOut,
  RiskLimitUpdate,
} from '@/services/risk/types';

export const RISK_LIMITS_QUERY_KEY = ['risk-limits'] as const;

export interface UpdateRiskLimitVariables {
  id: number;
  body: RiskLimitUpdate;
}

export interface UseRiskLimitsResult {
  list: UseQueryResult<RiskLimitOut[], Error>;
  create: UseMutationResult<RiskLimitOut, Error, RiskLimitCreate>;
  update: UseMutationResult<RiskLimitOut, Error, UpdateRiskLimitVariables>;
  remove: UseMutationResult<undefined, Error, number>;
}

export function useRiskLimits(): UseRiskLimitsResult {
  const queryClient = useQueryClient();

  const list = useQuery<RiskLimitOut[], Error>({
    queryKey: RISK_LIMITS_QUERY_KEY,
    queryFn: listRiskLimits,
    staleTime: 30_000,
  });

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: RISK_LIMITS_QUERY_KEY });
  };

  const create = useMutation<RiskLimitOut, Error, RiskLimitCreate>({
    mutationFn: createRiskLimit,
    onSuccess: invalidate,
  });

  const update = useMutation<RiskLimitOut, Error, UpdateRiskLimitVariables>({
    mutationFn: ({ id, body }) => updateRiskLimit(id, body),
    onSuccess: invalidate,
  });

  const remove = useMutation<undefined, Error, number>({
    mutationFn: deleteRiskLimit,
    onSuccess: invalidate,
  });

  return { list, create, update, remove };
}
