/**
 * Frontend types for the /api/brokers/{broker_id}/capabilities endpoint.
 *
 * Phase 10a D6: BE response_model is pinned to BrokerCapabilitiesResponse
 * and the polymorphic flat-list / grouped-dict legacy shape was removed.
 * These types are the canonical shape; the FE and BE agree at runtime.
 */

export interface OrderTypeRow {
  code: string;
  label: string;
  description: string;
  sort_order: number;
}

export interface TimeInForceRow {
  code: string;
  label: string;
  description: string;
  requires_expiry: boolean;
  sort_order: number;
}

export interface CapabilityComboRow {
  broker_id: string;
  asset_class: string;
  order_type: string;
  time_in_force: string;
  supported: boolean;
  notes: string;
}

export interface BrokerCapabilitiesResponse {
  broker_id: string;
  // typescript-reviewer MED: readonly arrays so consumers don't accidentally
  // mutate the response payload (CLAUDE.md immutability discipline).
  order_types: readonly OrderTypeRow[];
  time_in_force: readonly TimeInForceRow[];
  combos: readonly CapabilityComboRow[];
}
