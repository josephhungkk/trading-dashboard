/**
 * Phase 10a E1 — TanStack Query hook over the account kill-switch
 * GET/POST endpoints.
 *
 * Spec §7 [M9]: setKillSwitch.onSuccess invalidates both the
 * account-specific query AND any list-level query that displays this
 * row (e.g. AccountsPage with embedded kill-switch column).
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import {
  getAccountKillSwitch,
  isRiskApiError,
  setAccountKillSwitch,
} from '@/services/risk/api';
import type {
  AccountKillSwitchOut,
  AccountKillSwitchToggleRequest,
} from '@/services/risk/types';

export const ACCOUNT_KILL_SWITCH_KEY = 'account-kill-switches';

export function accountKillSwitchQueryKey(accountId: string): readonly [string, string] {
  return [ACCOUNT_KILL_SWITCH_KEY, accountId] as const;
}

export interface UseAccountKillSwitchResult {
  query: UseQueryResult<AccountKillSwitchOut | null, Error>;
  setKillSwitch: UseMutationResult<
    AccountKillSwitchOut,
    Error,
    AccountKillSwitchToggleRequest
  >;
}

export function useAccountKillSwitch(
  accountId: string,
): UseAccountKillSwitchResult {
  const queryClient = useQueryClient();

  const query = useQuery<AccountKillSwitchOut | null, Error>({
    queryKey: accountKillSwitchQueryKey(accountId),
    queryFn: async () => {
      try {
        return await getAccountKillSwitch(accountId);
      } catch (caught) {
        // 404 = no row exists = switch is implicitly off. Surface as
        // `null` rather than an error so consumers render the off
        // state cleanly. Anything else (403, 500, network) propagates.
        // E7-fix (ts HIGH): narrow via isRiskApiError type guard instead
        // of `as Partial<>` cast — keeps strict mode happy without losing
        // the unknown-error fallback.
        if (isRiskApiError(caught) && caught.status === 404) {
          return null;
        }
        throw caught;
      }
    },
    staleTime: 10_000,
    enabled: accountId.length > 0,
  });

  const setKillSwitch = useMutation<
    AccountKillSwitchOut,
    Error,
    AccountKillSwitchToggleRequest
  >({
    mutationFn: (body) => setAccountKillSwitch(accountId, body),
    onSuccess: () => {
      // E7-fix (code-quality M4): invalidate both the per-account
      // detail key AND the parent list key, so AccountsPage's column
      // refreshes when the toggle flips.
      void queryClient.invalidateQueries({
        queryKey: accountKillSwitchQueryKey(accountId),
      });
      void queryClient.invalidateQueries({
        queryKey: [ACCOUNT_KILL_SWITCH_KEY],
      });
    },
  });

  return { query, setKillSwitch };
}
