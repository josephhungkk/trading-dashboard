import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { EarningsBadge } from '../EarningsBadge'

vi.mock('../../../services/earnings/api', () => ({
  getInstrumentEarnings: vi.fn(async () => ({
    items: [
      {
        id: 'event-1',
        instrument_id: 1,
        canonical_id: 'equity_us:AAPL:NASDAQ',
        announced_date: new Date(Date.now() + 3 * 86_400_000).toISOString().slice(0, 10),
        time_of_day: 'after_close',
        source: 'nasdaq_api',
        source_priority: 2,
        confirmed: false,
        captured_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ],
  })),
}))

function renderBadge(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <EarningsBadge instrumentId={1} />
    </QueryClientProvider>,
  )
}

describe('EarningsBadge', () => {
  it('renders upcoming earnings badge', async () => {
    renderBadge()
    expect(await screen.findByRole('button', { name: /earnings in/i })).toHaveTextContent(
      /Earnings in \d+d \(AMC\)/,
    )
  })
})
