import { useQuery } from '@tanstack/react-query'

import { getInstrumentEarnings } from '../../services/earnings/api'

interface EarningsBadgeProps {
  instrumentId: number
  onClick?: () => void
}

const timeLabels = {
  before_open: 'BMO',
  after_close: 'AMC',
  during_market: 'DMT',
  unknown: 'TBA',
} as const

function daysUntil(dateValue: string): number {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const target = new Date(`${dateValue}T00:00:00`)
  return Math.ceil((target.getTime() - today.getTime()) / 86_400_000)
}

export function EarningsBadge({ instrumentId, onClick }: EarningsBadgeProps) {
  const { data } = useQuery({
    queryKey: ['earnings', 'instrument', instrumentId],
    queryFn: () => getInstrumentEarnings(instrumentId),
    staleTime: 5 * 60 * 1000,
  })

  const upcoming = (data ?? [])
    .filter((event) => {
      const days = daysUntil(event.announced_date)
      return days >= 0 && days <= 7
    })
    .sort((a, b) => a.announced_date.localeCompare(b.announced_date))[0]

  if (!upcoming) return null

  const days = daysUntil(upcoming.announced_date)
  const time = timeLabels[upcoming.time_of_day ?? 'unknown']

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Earnings in ${days} days, ${time}`}
      className="inline-flex h-7 items-center rounded border border-amber-300 bg-amber-100 px-2 text-xs font-medium text-amber-900"
    >
      Earnings in {days}d ({time})
    </button>
  )
}
