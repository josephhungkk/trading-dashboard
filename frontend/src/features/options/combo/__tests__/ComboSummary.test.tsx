import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ComboSummary } from '../ComboSummary'

describe('ComboSummary', () => {
  it('renders net debit amount', () => {
    render(
      <ComboSummary
        envelope={{
          net_debit_credit: '3.10000000',
          kind: 'DEBIT',
          max_loss: '310.00000000',
          max_profit: '690.00000000',
          break_even: ['253.10000000'],
        }}
      />,
    )
    expect(screen.getAllByText(/3\.10/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Net Debit/)).toBeInTheDocument()
    expect(screen.getByText(/Max loss/)).toBeInTheDocument()
  })
})
