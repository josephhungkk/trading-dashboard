import type { Filing, FilingsQuery } from "./types"

const BASE = "/api/filings"

export const filingsApi = {
  async list(query: FilingsQuery = {}): Promise<Filing[]> {
    const params = new URLSearchParams()
    if (query.canonical_id) params.set("canonical_id", query.canonical_id)
    if (query.source) params.set("source", query.source)
    if (query.limit) params.set("limit", String(query.limit))
    if (query.offset) params.set("offset", String(query.offset))
    const res = await fetch(`${BASE}?${params}`)
    if (!res.ok) throw new Error(`filings list failed: ${res.status}`)
    return res.json() as Promise<Filing[]>
  },

  async get(id: string): Promise<Filing> {
    const res = await fetch(`${BASE}/${id}`)
    if (!res.ok) throw new Error(`filing not found: ${res.status}`)
    return res.json() as Promise<Filing>
  },

  async triggerPoll(): Promise<void> {
    const res = await fetch(`${BASE}/poll`, { method: "POST" })
    if (!res.ok) throw new Error(`poll failed: ${res.status}`)
  },
}
