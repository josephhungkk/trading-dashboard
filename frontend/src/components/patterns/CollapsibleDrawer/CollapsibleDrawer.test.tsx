import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as React from 'react';
import { CollapsibleDrawer } from './CollapsibleDrawer';

// Radix Dialog uses PointerEvents, hasPointerCapture, and ResizeObserver
// which jsdom doesn't implement. Stub just enough for the drawer to open,
// close, and receive pointer events.
function stubJsdomPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') {
    proto['hasPointerCapture'] = () => false;
  }
  if (typeof proto['releasePointerCapture'] !== 'function') {
    proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['setPointerCapture'] !== 'function') {
    proto['setPointerCapture'] = () => { /* jsdom stub */ };
  }
  if (typeof proto['scrollIntoView'] !== 'function') {
    proto['scrollIntoView'] = () => { /* jsdom stub */ };
  }
  const g = globalThis as unknown as { ResizeObserver?: unknown };
  if (typeof g.ResizeObserver !== 'function') {
    g.ResizeObserver = class {
      observe(): void { /* jsdom stub */ }
      unobserve(): void { /* jsdom stub */ }
      disconnect(): void { /* jsdom stub */ }
    };
  }
}

interface HarnessProps {
  side: 'left' | 'right';
  initialOpen: boolean;
  onOpenChange?: (open: boolean) => void;
}

function Harness({ side, initialOpen, onOpenChange }: HarnessProps): React.JSX.Element {
  const [open, setOpen] = React.useState<boolean>(initialOpen);
  const handleChange = (next: boolean): void => {
    onOpenChange?.(next);
    setOpen(next);
  };
  return (
    <CollapsibleDrawer open={open} onOpenChange={handleChange} side={side} title="Test drawer">
      <div data-testid="drawer-body">Drawer body</div>
    </CollapsibleDrawer>
  );
}

function renderDrawer(
  side: 'left' | 'right',
  onOpenChange?: (open: boolean) => void,
): void {
  stubJsdomPointer();
  // exactOptionalPropertyTypes: only pass onOpenChange when defined so the
  // optional prop isn't assigned `undefined` explicitly.
  const harness =
    onOpenChange !== undefined
      ? <Harness side={side} initialOpen={true} onOpenChange={onOpenChange} />
      : <Harness side={side} initialOpen={true} />;
  render(harness);
}

describe('CollapsibleDrawer', () => {
  it('opens when open=true', async () => {
    renderDrawer('left');
    await waitFor(() => {
      expect(screen.getByTestId('drawer-body')).toBeInTheDocument();
    });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('closes when Escape pressed', async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderDrawer('left', onOpenChange);
    await waitFor(() => {
      expect(screen.getByTestId('drawer-body')).toBeInTheDocument();
    });
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });

  it('closes when overlay is clicked', async () => {
    const onOpenChange = vi.fn();
    renderDrawer('left', onOpenChange);
    const overlay = await screen.findByTestId('collapsible-drawer-overlay');
    // Radix binds pointerdown on the overlay; drive the pointer sequence
    // through userEvent so the synthetic PointerEvent carries button + type.
    const user = userEvent.setup();
    await user.pointer({ keys: '[MouseLeft]', target: overlay });
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });

  it('applies left-side translate and border classes when side=left', async () => {
    renderDrawer('left');
    const content = await screen.findByTestId('collapsible-drawer-content');
    const cls = content.className;
    expect(cls).toContain('-translate-x-full');
    expect(cls).toContain('border-r');
    expect(cls).toContain('left-0');
  });

  it('applies right-side translate and border classes when side=right', async () => {
    renderDrawer('right');
    const content = await screen.findByTestId('collapsible-drawer-content');
    const cls = content.className;
    expect(cls).toContain('translate-x-full');
    expect(cls).not.toContain('-translate-x-full');
    expect(cls).toContain('border-l');
    expect(cls).toContain('right-0');
  });
});
