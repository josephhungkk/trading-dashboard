import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ResizablePanelFrame } from './ResizablePanelFrame';

// jsdom doesn't implement ResizeObserver — stub it. react-resizable-panels
// observes its container to compute panel pixel sizes.
class ResizeObserverStub {
  observe(): void {
    /* noop */
  }
  unobserve(): void {
    /* noop */
  }
  disconnect(): void {
    /* noop */
  }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const panelsBasic = [
  { id: 'a', defaultSize: 50, content: <div>Panel A content</div> },
  { id: 'b', defaultSize: 50, content: <div>Panel B content</div> },
];

const panelsCollapsible = [
  {
    id: 'sidebar',
    defaultSize: 25,
    collapsible: true,
    collapsedSize: 3,
    content: <div>Sidebar</div>,
  },
  { id: 'main', defaultSize: 75, content: <div>Main</div> },
];

describe('ResizablePanelFrame', () => {
  it('renders all panel contents', () => {
    render(<ResizablePanelFrame direction="horizontal" panels={panelsBasic} />);
    expect(screen.getByText('Panel A content')).toBeInTheDocument();
    expect(screen.getByText('Panel B content')).toBeInTheDocument();
  });

  it('renders a resize handle between panels', () => {
    const { container } = render(
      <ResizablePanelFrame direction="horizontal" panels={panelsBasic} />,
    );
    // react-resizable-panels gives resize handles role="separator".
    const separators = container.querySelectorAll('[role="separator"]');
    expect(separators.length).toBeGreaterThan(0);
  });

  it('renders a toggle button for collapsible panels', () => {
    render(<ResizablePanelFrame direction="horizontal" panels={panelsCollapsible} />);
    expect(screen.getByRole('button', { name: /Toggle sidebar/i })).toBeInTheDocument();
  });

  it('does not render a toggle button for non-collapsible panels', () => {
    render(<ResizablePanelFrame direction="horizontal" panels={panelsBasic} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('toggle button click invokes collapse/expand without throwing', async () => {
    const user = userEvent.setup();
    render(<ResizablePanelFrame direction="horizontal" panels={panelsCollapsible} />);
    const btn = screen.getByRole('button', { name: /Toggle sidebar/i });
    // Verify it's clickable; actual collapse state is hard to assert in jsdom
    // because react-resizable-panels relies on ResizeObserver for pixel math.
    await expect(user.click(btn)).resolves.not.toThrow();
  });
});
