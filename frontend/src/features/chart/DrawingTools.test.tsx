import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DrawingTools, DRAWING_TOOLS, MOBILE_PRIORITY } from './DrawingTools';
import { useChartStore } from './stores/chartStore';

function resetStore(): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators: [],
    drawings: [],
    chartType: 'candle',
    activeDrawingTool: null,
  });
}

describe('DrawingTools', () => {
  beforeEach(() => {
    resetStore();
  });

  it('renders all verified drawing tools as buttons', () => {
    render(<DrawingTools />);

    for (const name of DRAWING_TOOLS) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
  });

  it('clicking a tool sets it as active (aria-pressed=true)', async () => {
    const user = userEvent.setup();
    render(<DrawingTools />);

    const btn = screen.getByRole('button', { name: 'priceLine' });
    expect(btn).toHaveAttribute('aria-pressed', 'false');

    await user.click(btn);

    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(useChartStore.getState().activeDrawingTool).toBe('priceLine');
  });

  it('clicking the active tool again deactivates it (aria-pressed=false)', async () => {
    const user = userEvent.setup();
    render(<DrawingTools />);

    const btn = screen.getByRole('button', { name: 'segment' });

    // Activate
    await user.click(btn);
    expect(btn).toHaveAttribute('aria-pressed', 'true');

    // Deactivate
    await user.click(btn);
    expect(btn).toHaveAttribute('aria-pressed', 'false');
    expect(useChartStore.getState().activeDrawingTool).toBeNull();
  });

  it('activating a different tool deactivates the previous one', async () => {
    const user = userEvent.setup();
    render(<DrawingTools />);

    const btnA = screen.getByRole('button', { name: 'rect' });
    const btnB = screen.getByRole('button', { name: 'circle' });

    await user.click(btnA);
    expect(btnA).toHaveAttribute('aria-pressed', 'true');

    await user.click(btnB);
    expect(btnA).toHaveAttribute('aria-pressed', 'false');
    expect(btnB).toHaveAttribute('aria-pressed', 'true');
    expect(useChartStore.getState().activeDrawingTool).toBe('circle');
  });

  it('renders mobile-priority tools and wraps the remaining tools for desktop', () => {
    render(<DrawingTools />);

    expect(MOBILE_PRIORITY).toHaveLength(7);
    for (const name of MOBILE_PRIORITY) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
    expect(screen.getByTestId('drawing-tools-desktop-rest')).toHaveClass('hidden', 'md:contents');
  });

  it('renders mobile More drawings trigger as mobile-only', () => {
    render(<DrawingTools />);
    expect(screen.getByRole('button', { name: 'More drawings' })).toHaveClass('md:hidden');
  });
});
