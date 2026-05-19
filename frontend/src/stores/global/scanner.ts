import { create } from "zustand"
import { persist } from "zustand/middleware"
import type { ScanCandidate, ScanRun, SavedScan } from "../../services/scanner/types"

interface ScannerState {
  savedScans: SavedScan[]
  activeRunId: string | null
  candidates: ScanCandidate[]
  runs: ScanRun[]
  setSavedScans: (scans: SavedScan[]) => void
  setActiveRunId: (id: string | null) => void
  addCandidate: (c: ScanCandidate) => void
  clearCandidates: () => void
  setRuns: (runs: ScanRun[]) => void
}

export const useScannerStore = create<ScannerState>()(
  persist(
    (set) => ({
      savedScans: [],
      activeRunId: null,
      candidates: [],
      runs: [],
      setSavedScans: (scans) => set({ savedScans: scans }),
      setActiveRunId: (id) => set({ activeRunId: id, candidates: [] }),
      addCandidate: (c) =>
        set((s) => ({ candidates: [...s.candidates, c].slice(-500) })),
      clearCandidates: () => set({ candidates: [] }),
      setRuns: (runs) => set({ runs }),
    }),
    {
      name: "scanner-store",
      partialize: (s) => ({ savedScans: s.savedScans }),
    },
  ),
)
