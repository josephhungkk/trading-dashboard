import { describe, expect, it } from 'vitest'

import type { EarningsEvent, EarningsHookCreate } from '../types'

describe('earnings types', () => {
  it('accepts an earnings event payload', () => {
    const event: EarningsEvent = {
      id: 'event-1',
      instrument_id: 1,
      canonical_id: 'equity_us:AAPL:NASDAQ',
      announced_date: '2026-05-20',
      time_of_day: 'after_close',
      source: 'nasdaq_api',
      source_priority: 2,
      confirmed: false,
      captured_at: '2026-05-19T10:00:00Z',
      updated_at: '2026-05-19T10:00:00Z',
    }

    expect(event.source).toBe('nasdaq_api')
  })

  it('accepts an auto-flat hook create payload', () => {
    const hook: EarningsHookCreate = {
      instrument_id: 1,
      account_id: '00000000-0000-0000-0000-000000000001',
      hook_type: 'auto_flat',
      minutes_before: 30,
    }

    expect(hook.minutes_before).toBe(30)
  })
})
