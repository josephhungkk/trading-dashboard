import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChartContextMenu } from './ChartContextMenu';
import { useChartStore } from './stores/chartStore';

const DEFAULT_POSITION = { x: 120, y: 240 };

function resetStore(indicators: string[] = []): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators,
    drawings: [],
    chartType: 'candle',
    activeDrawingTool: null,
  });
}

function makeProps(
  overrides: Partial<React.ComponentProps<typeof ChartContextMenu>> = {},
): React.ComponentProps<typeof ChartContextMenu> {
  return {
    open: true,
    position: DEFAULT_POSITION,
    onClose: vi.fn(),
    onAddIndicator: vi.fn(),
    onCopySnapshot: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe('ChartContextMenu', () => {
  beforeEach(() => {
    resetStore();
  });

  it('does not render when open=false', () => {
    const props = makeProps({ open: false });
    render(<ChartContextMenu {...props} />);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('renders at the given pixel position', () => {
    const props = makeProps();
    render(<ChartContextMenu {...props} />);
    const menu = screen.getByRole('menu', { name: 'Chart context menu' });
    expect(menu).toHaveStyle({ left: '120px', top: '240px', position: 'fixed' });
  });

  it('calls onAddIndicator and onClose when "Add Indicator" is clicked', async () => {
    const user = userEvent.setup();
    const props = makeProps();
    render(<ChartContextMenu {...props} />);

    await user.click(screen.getByRole('menuitem', { name: 'Add Indicator' }));

    expect(props.onAddIndicator).toHaveBeenCalledTimes(1);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('does not show "Remove Indicator" when no indicators are active', () => {
    resetStore([]);
    const props = makeProps();
    render(<ChartContextMenu {...props} />);
    expect(screen.queryByRole('menuitem', { name: /Remove Indicator/i })).toBeNull();
  });

  it('shows remove submenu with current indicators and removes on click', async () => {
    const user = userEvent.setup();
    resetStore(['MA', 'RSI']);
    const props = makeProps();
    render(<ChartContextMenu {...props} />);

    // Open the submenu
    await user.click(screen.getByRole('menuitem', { name: /Remove Indicator/i }));

    expect(screen.getByRole('menuitem', { name: 'MA' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'RSI' })).toBeInTheDocument();

    // Click to remove MA
    await user.click(screen.getByRole('menuitem', { name: 'MA' }));

    expect(useChartStore.getState().indicators).toEqual(['RSI']);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onCopySnapshot and onClose when "Copy Snapshot" is clicked', async () => {
    const user = userEvent.setup();
    const props = makeProps();
    render(<ChartContextMenu {...props} />);

    await user.click(screen.getByRole('menuitem', { name: 'Copy Snapshot' }));

    expect(props.onCopySnapshot).toHaveBeenCalledTimes(1);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when Escape is pressed', () => {
    const props = makeProps();
    render(<ChartContextMenu {...props} />);

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when clicking outside the menu', async () => {
    const user = userEvent.setup();
    const props = makeProps();
    render(
      <div>
        <ChartContextMenu {...props} />
        <button type="button" data-testid="outside">outside</button>
      </div>,
    );

    await user.click(screen.getByTestId('outside'));

    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('stale-closure regression: calls latest onClose after prop update', () => {
    // MED-A regression: if onClose ref is not kept fresh, the original (stale)
    // callback is called after a re-render with a new onClose prop.
    const firstClose = vi.fn();
    const secondClose = vi.fn();

    const { rerender } = render(
      <ChartContextMenu
        open={true}
        position={DEFAULT_POSITION}
        onClose={firstClose}
        onAddIndicator={vi.fn()}
        onCopySnapshot={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    // Re-render with a *different* onClose before the menu is dismissed.
    rerender(
      <ChartContextMenu
        open={true}
        position={DEFAULT_POSITION}
        onClose={secondClose}
        onAddIndicator={vi.fn()}
        onCopySnapshot={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    // Dismiss via Escape — should call the *latest* onClose, not the stale one.
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(firstClose).not.toHaveBeenCalled();
    expect(secondClose).toHaveBeenCalledTimes(1);
  });
});
