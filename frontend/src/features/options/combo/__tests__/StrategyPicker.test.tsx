import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StrategyPicker } from '../StrategyPicker'

describe('StrategyPicker', () => {
  it('renders all 5 strategies', () => {
    render(<StrategyPicker value="VERTICAL" onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('combobox'))
    expect(screen.getAllByText('Vertical').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Straddle')).toBeInTheDocument()
  })
})
