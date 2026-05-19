import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { listEarnings } from '../../services/earnings/api'
import type { EarningsEvent } from '../../services/earnings/types'

const timeLabels = {
  before_open: 'BMO',
  after_close: 'AMC',
  during_market: 'DMT',
  unknown: 'TBA',
} as const

function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

function addDaysIso(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

function epsClass(event: EarningsEvent): string {
  if (event.eps_actual == null || event.eps_estimate == null) return ''
  const actual = Number(event.eps_actual)
  const estimate = Number(event.eps_estimate)
  if (Number.isNaN(actual) || Number.isNaN(estimate)) return ''
  return actual >= estimate ? 'text-green-700' : 'text-red-700'
}

export function EarningsPage() {
  const [dateFrom, setDateFrom] = useState(todayIso())
  const [dateTo, setDateTo] = useState(addDaysIso(14))
  const query = useMemo(
    () => ({ date_from: dateFrom, date_to: dateTo, limit: 200 }),
    [dateFrom, dateTo],
  )
  const { data, isLoading, error } = useQuery({
    queryKey: ['earnings', 'list', query],
    queryFn: () => listEarnings(query),
  })

  return (
    <main className="p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-xl font-semibold">Earnings</h1>
          <p className="mt-1 text-sm text-muted-foreground">Calendar events and estimates</p>
        </div>
        <label className="ml-auto block text-sm">
          <span className="mb-1 block font-medium">From</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
            className="rounded border bg-background px-2 py-1"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium">To</span>
          <input
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
            className="rounded border bg-background px-2 py-1"
          />
        </label>
      </div>

      <div className="mt-5 overflow-x-auto border">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-muted/50 text-left">
            <tr>
              <th className="px-3 py-2 font-medium">Date</th>
              <th className="px-3 py-2 font-medium">Symbol</th>
              <th className="px-3 py-2 font-medium">Time</th>
              <th className="px-3 py-2 font-medium">EPS Est</th>
              <th className="px-3 py-2 font-medium">EPS Actual</th>
              <th className="px-3 py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td className="px-3 py-4 text-muted-foreground" colSpan={6}>
                  Loading earnings...
                </td>
              </tr>
            ) : error ? (
              <tr>
                <td className="px-3 py-4 text-red-600" colSpan={6}>
                  Could not load earnings.
                </td>
              </tr>
            ) : (data?.items ?? []).length === 0 ? (
              <tr>
                <td className="px-3 py-4 text-muted-foreground" colSpan={6}>
                  No earnings in this range.
                </td>
              </tr>
            ) : (
              data?.items.map((event) => (
                <tr key={event.id} className="border-t">
                  <td className="px-3 py-2">{event.announced_date}</td>
                  <td className="px-3 py-2 font-medium">{event.canonical_id}</td>
                  <td className="px-3 py-2">{timeLabels[event.time_of_day ?? 'unknown']}</td>
                  <td className="px-3 py-2">{event.eps_estimate ?? 'n/a'}</td>
                  <td className={`px-3 py-2 ${epsClass(event)}`}>
                    {event.eps_actual ?? 'n/a'}
                  </td>
                  <td className="px-3 py-2">{event.source}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </main>
  )
}
