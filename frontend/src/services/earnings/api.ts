import { adminFetch, mintCsrfNonce } from '@/services/admin/api'

import type { EarningsEvent, EarningsHook, EarningsHookCreate } from './types'

export interface EarningsListParams {
  instrument_id?: number
  date_from?: string
  date_to?: string
  limit?: number
}

function queryString(params: EarningsListParams): string {
  const q = new URLSearchParams()
  if (params.instrument_id !== undefined) q.set('instrument_id', String(params.instrument_id))
  if (params.date_from) q.set('date_from', params.date_from)
  if (params.date_to) q.set('date_to', params.date_to)
  if (params.limit !== undefined) q.set('limit', String(params.limit))
  const encoded = q.toString()
  return encoded ? `?${encoded}` : ''
}

export async function listEarnings(
  params: EarningsListParams = {},
): Promise<{ items: EarningsEvent[] }> {
  return adminFetch<{ items: EarningsEvent[] }>(`/api/earnings${queryString(params)}`, {
    method: 'GET',
  })
}

export async function getInstrumentEarnings(
  instrumentId: number,
): Promise<{ items: EarningsEvent[] }> {
  return adminFetch<{ items: EarningsEvent[] }>(`/api/instruments/${instrumentId}/earnings`, {
    method: 'GET',
  })
}

export async function createEarningsHook(body: EarningsHookCreate): Promise<EarningsHook> {
  const nonce = await mintCsrfNonce()
  return adminFetch<EarningsHook>('/api/earnings/hooks', {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'X-Confirm-Nonce': nonce },
  })
}

export async function listEarningsHooks(): Promise<{ items: EarningsHook[] }> {
  return adminFetch<{ items: EarningsHook[] }>('/api/earnings/hooks', { method: 'GET' })
}

export async function deleteEarningsHook(id: string): Promise<void> {
  const nonce = await mintCsrfNonce()
  await adminFetch<undefined>(`/api/earnings/hooks/${id}`, {
    method: 'DELETE',
    headers: { 'X-Confirm-Nonce': nonce },
  })
}
