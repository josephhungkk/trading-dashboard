import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RightPanel } from './RightPanel';

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

// jsdom doesn't implement matchMedia — the compact feature stubs embed
// DataTable, which calls useMediaQuery for its mobile breakpoint.
function mkMql(matches: boolean, q: string): MediaQueryList {
  return {
    matches,
    media: q,
    onchange: null,
    addListener: () => { /* noop */ },
    removeListener: () => { /* noop */ },
    addEventListener: () => { /* noop */ },
    removeEventListener: () => { /* noop */ },
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}
window.matchMedia = (q: string) => mkMql(q.includes('min-width'), q);

function SizedWrapper({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <div className="h-[40rem] w-72">{children}</div>;
}

describe('RightPanel', () => {
  it('renders two nested panels with the feature stubs', () => {
    render(
      <SizedWrapper>
        <RightPanel />
      </SizedWrapper>,
    );
    expect(screen.getByText('Open Orders')).toBeInTheDocument();
    expect(screen.getByText('Positions')).toBeInTheDocument();
  });

  it('renders a vertical PanelGroup', () => {
    const { container } = render(
      <SizedWrapper>
        <RightPanel />
      </SizedWrapper>,
    );
    // react-resizable-panels v7+ marks the group with data-group and
    // encodes direction via inline flex-direction ("column" = vertical).
    const group = container.querySelector<HTMLElement>('[data-group]');
    expect(group).not.toBeNull();
    expect(group?.style.flexDirection).toBe('column');
  });
});
