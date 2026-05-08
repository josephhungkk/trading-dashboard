/**
 * Frontend types for the /api/brokers/{broker_id}/capabilities endpoint.
 *
 * These were previously imported from generated `components['schemas']`,
 * but the backend declares the BrokerCapabilitiesResponse Pydantic model
 * without using it as the endpoint's response_model — the actual return
 * type is `dict[str, list[dict]] | list[dict]` straight from the service
 * layer. openapi-typescript no longer surfaces those models, so the
 * imports broke when api-generated.ts was regenerated.
 *
 * KNOWN ISSUE: the FE consumer (useBrokerCapabilities.ts) expects the
 * model-declared shape below, but the BE returns flat capability rows
 * (or grouped-by-asset_class dict). Tests pass because they mock the
 * documented shape. At runtime against the real endpoint,
 * `query.data.combos` would be undefined. Tracked as a follow-up:
 * either bring the BE response back in line with this shape, or rewrite
 * the hook + consumers to handle the actual flat-list shape.
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
  order_type: string;
  time_in_force: string;
  supported: boolean;
  notes: string;
}

export interface BrokerCapabilitiesResponse {
  broker_id: string;
  order_types: OrderTypeRow[];
  time_in_force: TimeInForceRow[];
  combos: CapabilityComboRow[];
}
