import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LeftPanel } from './LeftPanel';

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

function SizedWrapper({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <div className="h-[40rem] w-72">{children}</div>;
}

describe('LeftPanel', () => {
  it('renders two nested panels with the feature stubs', () => {
    render(
      <SizedWrapper>
        <LeftPanel />
      </SizedWrapper>,
    );
    expect(screen.getByText('Account Summary')).toBeInTheDocument();
    expect(screen.getByText('Watchlist')).toBeInTheDocument();
  });

  it('renders a vertical PanelGroup', () => {
    const { container } = render(
      <SizedWrapper>
        <LeftPanel />
      </SizedWrapper>,
    );
    // react-resizable-panels v7+ marks the group with data-group and
    // encodes direction via inline flex-direction ("column" = vertical).
    const group = container.querySelector<HTMLElement>('[data-group]');
    expect(group).not.toBeNull();
    expect(group?.style.flexDirection).toBe('column');
  });
});
