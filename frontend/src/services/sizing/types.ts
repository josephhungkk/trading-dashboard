/**
 * Phase 10b.1 — frontend types for the position-sizing service.
 *
 * Re-exports the openapi-typescript generated schemas from api-generated.ts
 * via stable named aliases (mirrors services/risk/types.ts).
 */

import type { components } from '@/services/api-generated';

export type SizingMethod = components['schemas']['SizingMethod'];
export type FixedFractionalInputs =
  components['schemas']['FixedFractionalInputs'];
export type RiskPerTradeInputs = components['schemas']['RiskPerTradeInputs'];
export type VolTargetedInputs = components['schemas']['VolTargetedInputs'];
export type SizingRequest = components['schemas']['SizingRequest'];
export type SizingResult = components['schemas']['SizingResult'];
export type SizingDefaults = components['schemas']['SizingDefaults'];
export type SizingDefaultsUpdate =
  components['schemas']['SizingDefaultsUpdate'];
export type MethodBreakdown = components['schemas']['MethodBreakdown'];
export type GateVerdict = components['schemas']['GateVerdict'];

export type SizingInputs =
  | FixedFractionalInputs
  | RiskPerTradeInputs
  | VolTargetedInputs;
