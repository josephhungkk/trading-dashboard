import { describe, expect, it } from "vitest"
import type { SavedScan } from "../types"

describe("scanner types", () => {
  it("SavedScan has required fields", () => {
    const scan: SavedScan = {
      id: "uuid",
      name: "RSI scan",
      universe_config: { type: "tickers", params: { tickers: ["AAPL"] } },
      rule_expr: "rsi(14) < 30",
      schedule: null,
      market_hours_gate: false,
      exchange: null,
      llm_depth: "quick",
      alert_id: null,
      enabled: true,
      created_at: "2026-05-19T00:00:00Z",
      updated_at: "2026-05-19T00:00:00Z",
    }
    expect(scan.name).toBe("RSI scan")
  })
})
