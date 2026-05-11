/**
 * Frontend types for the Phase 10a risk surfaces.
 *
 * Re-exports the openapi-typescript generated schemas so consumers
 * import from a stable path (`@/services/risk/types`) regardless of
 * how api-generated.ts is regenerated.
 */

import type { components } from '@/services/api-generated';

export type RiskLimitOut = components['schemas']['RiskLimitOut'];
export type RiskLimitCreate = components['schemas']['RiskLimitCreate'];
export type RiskLimitUpdate = components['schemas']['RiskLimitUpdate'];
export type RiskDecisionOut = components['schemas']['RiskDecisionOut'];
export type AccountKillSwitchOut = components['schemas']['AccountKillSwitchOut'];
export type AccountKillSwitchToggleRequest =
  components['schemas']['AccountKillSwitchToggleRequest'];

export type RiskScopeType = 'global' | 'broker' | 'account';
export type RiskLimitKind =
  | 'max_daily_loss_currency_base'
  | 'max_position_concentration_pct'
  | 'pdt_warn_remaining'
  | 'min_buying_power_buffer_pct';
export type RiskVerdict = 'allow' | 'warn' | 'block';
export type RiskAttemptKind = 'place_order' | 'modify_order';

export interface RiskDecisionsFilter {
  account_id?: string;
  verdict?: RiskVerdict;
  limit?: number;
}
