import type { CreateScanPayload, SavedScan, ScanCandidate, ScanRun } from "./types"

const BASE = "/api/scanner"

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json() as Promise<T>
}

export const scannerApi = {
  validate: (rule_expr: string) =>
    fetchJson<{ valid: boolean; error?: string }>(`${BASE}/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rule_expr }),
    }),

  createScan: (payload: CreateScanPayload) =>
    fetchJson<{ id: string }>(`${BASE}/scans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  listScans: () => fetchJson<SavedScan[]>(`${BASE}/scans`),
  getScan: (id: string) => fetchJson<SavedScan>(`${BASE}/scans/${id}`),

  updateScan: (id: string, payload: CreateScanPayload) =>
    fetchJson<{ id: string }>(`${BASE}/scans/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  deleteScan: (id: string) => fetch(`${BASE}/scans/${id}`, { method: "DELETE" }),

  triggerRun: (scanId: string) =>
    fetchJson<{ run_id: string }>(`${BASE}/scans/${scanId}/run`, { method: "POST" }),

  adhocRun: (payload: CreateScanPayload) =>
    fetchJson<{ run_id: string }>(`${BASE}/runs/adhoc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  listRuns: (scanId?: string, cursor?: string) => {
    const params = new URLSearchParams()
    if (scanId) params.set("scan_id", scanId)
    if (cursor) params.set("cursor", cursor)
    return fetchJson<ScanRun[]>(`${BASE}/runs?${params.toString()}`)
  },

  getRun: (runId: string) =>
    fetchJson<ScanRun & { candidates: ScanCandidate[] }>(`${BASE}/runs/${runId}`),

  getCandidates: (runId: string, cursor?: string) => {
    const params = new URLSearchParams()
    if (cursor) params.set("cursor", cursor)
    return fetchJson<ScanCandidate[]>(`${BASE}/runs/${runId}/candidates?${params.toString()}`)
  },
}
