import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TimeframeBar } from './TimeframeBar';
import { useChartStore } from './stores/chartStore';

describe('TimeframeBar', () => {
  beforeEach(() => {
    useChartStore.setState({ timeframe: '1m' });
  });

  it('renders interval buttons for all 14 intervals', () => {
    render(<TimeframeBar />);
    const intervals = [
      '1s', '5s', '10s', '15s', '30s', '45s',
      '1m', '5m', '15m', '30m', '1h', '1d', '1w', '1M',
    ];
    for (const tf of intervals) {
      expect(
        screen.getByRole('button', { name: `Interval ${tf}` }),
      ).toBeInTheDocument();
    }
  });

  it('active interval has aria-pressed true', () => {
    useChartStore.setState({ timeframe: '5m' });
    render(<TimeframeBar />);
    expect(screen.getByRole('button', { name: 'Interval 5m' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Interval 1m' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('clicking an interval calls setTimeframe in the store', () => {
    render(<TimeframeBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Interval 1h' }));
    expect(useChartStore.getState().timeframe).toBe('1h');
  });

  it('clicking a different interval updates aria-pressed', () => {
    render(<TimeframeBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Interval 1d' }));
    expect(screen.getByRole('button', { name: 'Interval 1d' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('renders timeframe group landmark', () => {
    render(<TimeframeBar />);
    expect(screen.getByRole('group', { name: 'Timeframe controls' })).toBeInTheDocument();
  });
});
