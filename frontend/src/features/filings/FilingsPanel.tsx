import { useEffect, useState } from "react"
import { filingsApi } from "../../services/filings/api"
import type { Filing } from "../../services/filings/types"

interface Props {
  canonicalId: string
}

export function FilingsPanel({ canonicalId }: Props) {
  const [filings, setFilings] = useState<Filing[]>([])

  useEffect(() => {
    void filingsApi.list({ canonical_id: canonicalId, limit: 10 }).then(setFilings)
  }, [canonicalId])

  if (filings.length === 0) return null

  return (
    <div className="mt-4">
      <h3 className="text-sm font-semibold mb-2">Recent Filings</h3>
      <ul className="space-y-2">
        {filings.map((f) => (
          <li key={f.id} className="text-sm">
            <a
              href={f.url}
              target="_blank"
              rel="noreferrer"
              className="font-medium hover:underline"
            >
              {f.title}
            </a>
            <div className="text-xs text-muted-foreground">
              {f.form_type} · {new Date(f.filing_date).toLocaleDateString()}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
