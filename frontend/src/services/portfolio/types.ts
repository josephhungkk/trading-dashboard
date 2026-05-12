/**
 * Phase 10b.2 — portfolio rollup types.
 * Most shapes flow from api-generated.ts (regenerated via scripts/gen-types.sh
 * whenever backend schemas change). Hand-curated extensions live at the bottom.
 */

import type { components } from '@/services/api-generated';

export type RollupLive = components['schemas']['RollupLive'];
export type RollupCurve = components['schemas']['RollupCurve'];
export type RollupDrill = components['schemas']['RollupDrill'];
export type PerAccount = components['schemas']['PerAccount'];
export type AssetClassExposure = components['schemas']['AssetClassExposure'];
export type InstrumentExposure = components['schemas']['InstrumentExposure'];
export type CurvePoint = components['schemas']['CurvePoint'];
export type BucketTotal = components['schemas']['BucketTotal'];

export type CurveWindow = 'intraday' | '30d' | '1y';
export type BaseCurrency = 'GBP' | 'USD' | 'EUR' | 'HKD' | 'JPY' | 'AUD';

export const SUPPORTED_BASES: readonly BaseCurrency[] = [
  'GBP',
  'USD',
  'EUR',
  'HKD',
  'JPY',
  'AUD',
];

/**
 * WebSocket frame shape from /ws/portfolio/rollup. Matches gateway's
 * version=1 schema (see backend/app/api/ws_portfolio.py).
 */
export interface RollupWsFrame {
  version: 1;
  type: 'snapshot' | 'stale';
  payload?: RollupLive;
  account_ids?: string[];
}
