import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ComboPayoffChart } from '../ComboPayoffChart'

describe('ComboPayoffChart', () => {
  it('renders SVG element', () => {
    const { container } = render(
      <ComboPayoffChart
        envelope={{
          net_debit_credit: '3.10000000',
          kind: 'DEBIT',
          max_loss: '310.00000000',
          max_profit: '690.00000000',
          break_even: ['253.10000000'],
        }}
        legs={[
          { strike: '250', put_call: 'C' },
          { strike: '260', put_call: 'C' },
        ]}
      />,
    )
    expect(container.querySelector('svg')).toBeTruthy()
  })
})
