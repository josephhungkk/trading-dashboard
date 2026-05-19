import { useEffect, useState } from "react"
import { filingsApi } from "../../services/filings/api"
import type { Filing } from "../../services/filings/types"

export function FilingsPage() {
  const [filings, setFilings] = useState<Filing[]>([])

  useEffect(() => {
    void filingsApi.list({ limit: 50 }).then(setFilings)
  }, [])

  return (
    <div className="p-4">
      <h1 className="text-xl font-semibold mb-4">Filings</h1>
      {filings.length === 0 ? (
        <p className="text-sm text-muted-foreground">No filings yet.</p>
      ) : (
        <ul className="space-y-2">
          {filings.map((f) => (
            <li key={f.id} className="rounded border p-3">
              <a
                href={f.url}
                target="_blank"
                rel="noreferrer"
                className="font-medium hover:underline"
              >
                {f.title}
              </a>
              <div className="text-xs text-muted-foreground mt-1">
                {f.source} · {f.form_type} · {new Date(f.filing_date).toLocaleDateString()}
              </div>
              {f.llm_summary && (
                <p className="text-sm mt-1">{f.llm_summary}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
