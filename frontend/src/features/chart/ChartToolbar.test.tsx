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

function makeToolbarProps(overrides: Partial<React.ComponentProps<typeof ChartToolbar>> = {}): React.ComponentProps<typeof ChartToolbar> {
  return {
    drawingsOpen: false,
    onToggleDrawings: vi.fn(),
    ...overrides,
  };
}

describe('ChartToolbar', () => {
  beforeEach(() => {
    useChartStore.setState({ chartType: 'candle', indicators: [] });
  });

  it('renders toolbar landmark with all action buttons', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.getByRole('toolbar', { name: 'Chart controls' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Indicators' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Drawings' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: /Toggle fullscreen/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Screenshot/i })).toBeInTheDocument();
  });

  it('does not render a manual Save button (auto-save via ChartLayoutSync)', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.queryByRole('button', { name: /^Save( layout)?$/i })).not.toBeInTheDocument();
  });

  it('screenshot button is disabled (coming soon placeholder)', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.getByRole('button', { name: /Screenshot/i })).toBeDisabled();
  });

  it('clicking Indicators button opens IndicatorPicker', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.queryByTestId('indicator-picker-mock')).not.toBeInTheDocument();
    const [indicatorsButton] = screen.getAllByRole('button', { name: 'Indicators' });
    if (!indicatorsButton) throw new Error('Expected Indicators button');
    fireEvent.click(indicatorsButton);
    expect(screen.getByTestId('indicator-picker-mock')).toBeInTheDocument();
  });

  it('chart type combobox reflects current store chartType', () => {
    useChartStore.setState({ chartType: 'area' });
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.getByRole('combobox', { name: /Chart type/i })).toHaveTextContent('Area');
  });

  it('chart type combobox is present and wired to store', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    const combo = screen.getByRole('combobox', { name: /Chart type/i });
    expect(combo).toBeInTheDocument();
  });

  it('drawings button reflects drawingsOpen via aria-pressed', () => {
    render(<ChartToolbar {...makeToolbarProps({ drawingsOpen: true })} />);
    for (const btn of screen.getAllByRole('button', { name: 'Drawings' })) {
      expect(btn).toHaveAttribute('aria-pressed', 'true');
    }
  });

  it('drawings button calls onToggleDrawings when clicked', () => {
    const onToggleDrawings = vi.fn();
    render(<ChartToolbar {...makeToolbarProps({ drawingsOpen: false, onToggleDrawings })} />);
    const [drawingsButton] = screen.getAllByRole('button', { name: 'Drawings' });
    if (!drawingsButton) throw new Error('Expected Drawings button');
    fireEvent.click(drawingsButton);
    expect(onToggleDrawings).toHaveBeenCalledTimes(1);
  });

  it('compact toolbar wrapper is mobile-only', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.getByTestId('chart-toolbar-compact')).toHaveClass('md:hidden');
  });

  it('full toolbar wrapper is desktop-only', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.getByTestId('chart-toolbar-full')).toHaveClass('hidden', 'md:flex');
  });

  it('clicking More options opens the overflow modal', () => {
    render(<ChartToolbar {...makeToolbarProps()} />);
    expect(screen.queryByRole('dialog', { name: 'More chart options' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'More options' }));
    expect(screen.getByRole('dialog', { name: 'More chart options' })).toBeInTheDocument();
  });
});
