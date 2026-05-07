import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { IndicatorPicker } from './IndicatorPicker';
import { useChartStore } from './stores/chartStore';

describe('IndicatorPicker', () => {
  const onOpenChange = vi.fn();

  beforeEach(() => {
    useChartStore.setState({ indicators: [] });
    onOpenChange.mockReset();
  });

  it('renders nothing when closed', () => {
    render(<IndicatorPicker open={false} onOpenChange={onOpenChange} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders dialog with three tabs when open', () => {
    render(<IndicatorPicker open={true} onOpenChange={onOpenChange} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Favorites' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Technicals' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Custom' })).toBeInTheDocument();
  });

  it('Cancel button calls onOpenChange(false) without committing to store', () => {
    render(<IndicatorPicker open={true} onOpenChange={onOpenChange} />);

    // Stage an indicator but cancel.
    const maCheckbox = screen.getByRole('checkbox', { name: 'MA' });
    fireEvent.click(maCheckbox);
    expect(maCheckbox).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(useChartStore.getState().indicators).toEqual([]);
  });

  it('Apply button commits staged indicators to store and closes', () => {
    render(<IndicatorPicker open={true} onOpenChange={onOpenChange} />);

    fireEvent.click(screen.getByRole('checkbox', { name: 'RSI' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'MACD' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(useChartStore.getState().indicators).toEqual(
      expect.arrayContaining(['RSI', 'MACD']),
    );
  });

  it('unchecking a pre-selected indicator removes it on Apply', () => {
    useChartStore.setState({ indicators: ['MA', 'RSI'] });
    render(<IndicatorPicker open={true} onOpenChange={onOpenChange} />);

    // Uncheck MA.
    fireEvent.click(screen.getByRole('checkbox', { name: 'MA' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    const stored = useChartStore.getState().indicators;
    expect(stored).not.toContain('MA');
    expect(stored).toContain('RSI');
  });

  it('Technicals tab lists all 27 built-in indicators as checkboxes', () => {
    render(<IndicatorPicker open={true} onOpenChange={onOpenChange} />);
    // Default tab is Technicals — 27 checkboxes expected.
    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes).toHaveLength(27);
  });
});
