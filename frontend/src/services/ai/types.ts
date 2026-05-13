/**
 * Phase 11a-D — AI router service types.
 * Completion shapes flow from api-generated.ts; websocket frames are
 * hand-curated because the WS endpoints are not represented in OpenAPI.
 */

import type { components } from '@/services/api-generated';

export type CompletionRequest = components['schemas']['CompletionRequest'];
export type CompletionResult = components['schemas']['CompletionResult'];
export type FallbackHop = components['schemas']['FallbackHop'];
export type AICapability = components['schemas']['AICapability'];

// api-generated.ts currently exposes the job endpoints as unknown records, so
// keep the FE contract narrow and explicit here.
export interface JobSubmitResponse {
  job_id: string;
}

export interface JobStatusResponse {
  job_id?: string;
  status?: string;
  state?: string;
  error_code?: string;
  model?: string;
  response?: Record<string, unknown>;
  fallback_chain?: FallbackHop[];
}

// Hand-curated — WS isn't in OpenAPI.
export type ChatWsFrame =
  | { version: 1; type: 'chunk'; text: string; request_id: string; fallback_chain?: FallbackHop[] }
  | { version: 1; type: 'done'; request_id: string | null; fallback_chain?: FallbackHop[] }
  | { version: 1; type: 'error'; error_class: string; message: string; fallback_chain?: FallbackHop[] };

export interface JobWsFrame {
  version: 1;
  type: 'state';
  state: string;
  job_id: string;
  // Allowlisted extras, matching backend ws_ai.py:_ALLOWED_EXTRA_KEYS.
  error_code?: string;
  model?: string;
  response?: Record<string, unknown>;
  fallback_chain?: FallbackHop[];
}

export type ChatRole = 'user' | 'assistant' | 'system';

export interface ChatMessage {
  role: ChatRole;
  content: string;
}
