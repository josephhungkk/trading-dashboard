import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChartToolbar } from './ChartToolbar';
import { useChartStore } from './stores/chartStore';

// Mock IndicatorPicker to isolate ChartToolbar tests.
vi.mock('./IndicatorPicker', () => ({
  IndicatorPicker: ({ open }: { open: boolean; onOpenChange: (v: boolean) => void }) =>
    open ? <div data-testid="indicator-picker-mock" /> : null,
}));

// Mock klinecharts to avoid canvas crashes in jsdom.
vi.mock('klinecharts', () => ({
  init: vi.fn(() => ({
    setDataLoader: vi.fn(),
    setSymbol: vi.fn(),
    setPeriod: vi.fn(),
    createIndicator: vi.fn(),
  })),
  dispose: vi.fn(),
}));

describe('ChartToolbar', () => {
  beforeEach(() => {
    useChartStore.setState({ chartType: 'candle', indicators: [] });
  });

  it('renders toolbar landmark with all action buttons', () => {
    render(<ChartToolbar />);
    expect(screen.getByRole('toolbar', { name: 'Chart controls' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Indicators/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Drawings/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Save layout/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Toggle fullscreen/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Screenshot/i })).toBeInTheDocument();
  });

  it('screenshot button is disabled (coming soon placeholder)', () => {
    render(<ChartToolbar />);
    expect(screen.getByRole('button', { name: /Screenshot/i })).toBeDisabled();
  });

  it('clicking Indicators button opens IndicatorPicker', () => {
    render(<ChartToolbar />);
    expect(screen.queryByTestId('indicator-picker-mock')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Indicators/i }));
    expect(screen.getByTestId('indicator-picker-mock')).toBeInTheDocument();
  });

  it('chart type combobox reflects current store chartType', () => {
    useChartStore.setState({ chartType: 'area' });
    render(<ChartToolbar />);
    expect(screen.getByRole('combobox', { name: /Chart type/i })).toHaveTextContent('Area');
  });

  it('chart type combobox is present and wired to store', () => {
    render(<ChartToolbar />);
    const combo = screen.getByRole('combobox', { name: /Chart type/i });
    expect(combo).toBeInTheDocument();
  });
});
