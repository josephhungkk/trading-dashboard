import type { components } from '@/services/api-generated';

export type CreateAlertRequest = components['schemas']['CreateAlertRequest'];

export interface AlertRule {
  id: number;
  user_label: string;
  original_nl: string;
  predicate_json: Record<string, unknown>;
  requires_capabilities: string[];
  parse_status: string;
  delivery_channels: string[];
  tick_subscribed: boolean;
  status: string;
  dormancy_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface ParseFailedResponse {
  id: null;
  parse_status: 'failed';
  partial_predicate: Record<string, unknown> | null;
  suggestions: string[];
}

export type CreateAlertResponse = AlertRule | ParseFailedResponse;

export function isParseFailed(
  resp: CreateAlertResponse,
): resp is ParseFailedResponse {
  return resp.id === null && resp.parse_status === 'failed';
}

export interface RecentFire {
  id: number;
  alert_id: number;
  fired_at: string;
  verdict: string;
}

export interface AlertWsFrame {
  v: 1;
  type: 'fire';
  fire_id: number;
  alert_id: number;
  user_label: string;
  verdict: string;
  evaluated_values: Record<string, unknown>;
  fired_at: string;
}
