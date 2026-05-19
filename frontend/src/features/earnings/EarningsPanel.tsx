import { useQuery } from '@tanstack/react-query'

import { getInstrumentEarnings } from '../../services/earnings/api'
import type { EarningsEvent } from '../../services/earnings/types'

interface EarningsPanelProps {
  instrumentId: number
}

function isUpcoming(event: EarningsEvent): boolean {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return new Date(`${event.announced_date}T00:00:00`) >= today
}

function formatDate(value: string): string {
  return new Date(`${value}T00:00:00`).toLocaleDateString()
}

export function EarningsPanel({ instrumentId }: EarningsPanelProps) {
  const { data, isLoading } = useQuery({
    queryKey: ['earnings', 'instrument', instrumentId],
    queryFn: () => getInstrumentEarnings(instrumentId),
    staleTime: 5 * 60 * 1000,
  })

  const events = data?.items ?? []
  const upcoming = events
    .filter(isUpcoming)
    .sort((a, b) => a.announced_date.localeCompare(b.announced_date))
    .slice(0, 1)
  const recent = events
    .filter((event) => !isUpcoming(event))
    .sort((a, b) => b.announced_date.localeCompare(a.announced_date))
    .slice(0, 4)

  if (isLoading) return <div className="text-sm text-muted-foreground">Loading earnings...</div>

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">Upcoming</h2>
        {upcoming.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No upcoming earnings.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {upcoming.map((event) => (
              <li key={event.id} className="rounded border p-3 text-sm">
                {formatDate(event.announced_date)} · {event.time_of_day ?? 'unknown'} ·{' '}
                {event.source}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h2 className="text-sm font-semibold">Recent</h2>
        {recent.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No recent earnings.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {recent.map((event) => (
              <li key={event.id} className="rounded border p-3 text-sm">
                {formatDate(event.announced_date)} · EPS {event.eps_actual ?? 'n/a'} /{' '}
                {event.eps_estimate ?? 'n/a'}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
