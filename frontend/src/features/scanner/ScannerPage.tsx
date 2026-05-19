import { useEffect } from "react"
import { scannerApi } from "../../services/scanner/api"
import { useScannerStore } from "../../stores/global/scanner"

export function ScannerPage() {
  const { savedScans, setSavedScans } = useScannerStore()

  useEffect(() => {
    void scannerApi.listScans().then(setSavedScans)
  }, [setSavedScans])

  return (
    <div className="p-4">
      <h1 className="text-xl font-semibold mb-4">Scanner</h1>
      {savedScans.length === 0 ? (
        <p className="text-sm text-muted-foreground">No saved scans. Create one to get started.</p>
      ) : (
        <ul className="space-y-2">
          {savedScans.map((scan) => (
            <li key={scan.id} className="rounded border p-3">
              <span className="font-medium">{scan.name}</span>
              <span className="ml-2 text-xs text-muted-foreground">{scan.rule_expr}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
