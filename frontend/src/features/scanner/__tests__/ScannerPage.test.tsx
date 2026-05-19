import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { ScannerPage } from "../ScannerPage"

vi.mock("../../../services/scanner/api", () => ({
  scannerApi: {
    listScans: vi.fn().mockResolvedValue([]),
  },
}))

vi.mock("../../../stores/global/scanner", () => ({
  useScannerStore: vi.fn(() => ({
    savedScans: [],
    setSavedScans: vi.fn(),
  })),
}))

describe("ScannerPage", () => {
  it("renders empty state", () => {
    render(<ScannerPage />)
    expect(screen.getByText("Scanner")).toBeInTheDocument()
    expect(screen.getByText(/no saved scans/i)).toBeInTheDocument()
  })
})
