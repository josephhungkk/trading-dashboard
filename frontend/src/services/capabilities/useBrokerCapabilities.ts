import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { BrokerCapabilitiesResponse } from './types';

interface BrokerCapabilitiesResult {
  data: BrokerCapabilitiesResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  isSupported: (orderType: string, timeInForce: string) => boolean;
  notesFor: (orderType: string, timeInForce: string) => string | undefined;
}

export function useBrokerCapabilities(brokerId: string | null): BrokerCapabilitiesResult {
  const queryClient = useQueryClient();
  const query = useQuery<BrokerCapabilitiesResponse>({
    queryKey: ['brokerCapabilities', brokerId],
    queryFn: () => fetchBrokerCapabilities(brokerId),
    enabled: brokerId !== null,
  });

  React.useEffect(() => {
    if (typeof EventSource === 'undefined') return undefined;
    // CRIT-4: correct path is /api/admin/config/stream (not /api/sse/config_stream).
    // withCredentials so the Cf-Access session cookie is sent for require_admin_jwt.
    // ns scopes the SSE stream to capability invalidations only.
    const url = `/api/admin/config/stream?ns=${encodeURIComponent('order_capabilities')}`;
    const eventSource = new EventSource(url, { withCredentials: true });
    const onMessage = (event: MessageEvent<string>): void => {
      if (!isCapabilityInvalidation(event.data)) return;
      queryClient.invalidateQueries({ queryKey: ['brokerCapabilities'] });
    };
    eventSource.addEventListener('message', onMessage);
    return () => {
      eventSource.removeEventListener('message', onMessage);
      eventSource.close();
    };
  }, [queryClient]);

  const comboMap = React.useMemo(() => {
    const map = new Map<string, { supported: boolean; notes?: string | null }>();
    for (const combo of query.data?.combos ?? []) {
      map.set(comboKey(combo.order_type, combo.time_in_force), {
        supported: combo.supported,
        notes: combo.notes ?? null,
      });
    }
    return map;
  }, [query.data]);

  const isSupported = React.useCallback((orderType: string, timeInForce: string): boolean => {
    if (brokerId === null) return false;
    return comboMap.get(comboKey(orderType, timeInForce))?.supported === true;
  }, [brokerId, comboMap]);

  const notesFor = React.useCallback((orderType: string, timeInForce: string): string | undefined => {
    const notes = comboMap.get(comboKey(orderType, timeInForce))?.notes;
    return notes === null ? undefined : notes;
  }, [comboMap]);

  return {
    data: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    isSupported,
    notesFor,
  };
}

async function fetchBrokerCapabilities(brokerId: string | null): Promise<BrokerCapabilitiesResponse> {
  if (brokerId === null) throw new Error('brokerId is required');
  const response = await fetch(`/api/brokers/${encodeURIComponent(brokerId)}/capabilities`);
  if (!response.ok) throw new Error(`broker capabilities ${response.status}`);
  const body = await response.json() as unknown;
  if (!isBrokerCapabilitiesResponse(body)) throw new Error('broker capabilities invalid response');
  return body;
}

function comboKey(orderType: string, timeInForce: string): string {
  return `${orderType}\u0000${timeInForce}`;
}

function isBrokerCapabilitiesResponse(value: unknown): value is BrokerCapabilitiesResponse {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Partial<Record<keyof BrokerCapabilitiesResponse, unknown>>;
  return typeof record.broker_id === 'string'
    && Array.isArray(record.order_types)
    && Array.isArray(record.time_in_force)
    && Array.isArray(record.combos);
}

function isCapabilityInvalidation(data: string): boolean {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return false;
  }
  if (typeof parsed !== 'object' || parsed === null) return false;
  const namespace = (parsed as { namespace?: unknown }).namespace;
  if (typeof namespace !== 'string') return false;
  const normalized = namespace.toLowerCase();
  return normalized.includes('capabilities') || normalized.includes('broker');
}
