import { describe, it, expect } from "vitest"
import type { Filing } from "../types"

describe("Filing type", () => {
  it("accepts a valid filing object", () => {
    const f: Filing = {
      id: "abc-123",
      instrument_id: null,
      canonical_id: "AAPL.XNAS",
      source: "sec_edgar",
      form_type: "8-K",
      filing_date: "2024-01-01T00:00:00Z",
      title: "Material Event",
      url: "https://sec.gov/test",
      llm_summary: null,
      captured_at: "2024-01-01T00:00:00Z",
    }
    expect(f.source).toBe("sec_edgar")
    expect(f.canonical_id).toBe("AAPL.XNAS")
  })
})
