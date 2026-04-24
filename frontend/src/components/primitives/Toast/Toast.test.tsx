import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { Toaster } from './Toast';
import { useToastStore } from '@/hooks/use-toast';

// Radix overlay primitives rely on PointerEvents and hasPointerCapture which
// jsdom does not implement. Stub just enough to let userEvent drive them.
function stubRadixPointer(): void {
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
}

describe('useToastStore', () => {
  beforeEach(() => {
    stubRadixPointer();
    useToastStore.setState({ items: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('push() appends an item and returns a non-empty id', () => {
    vi.useFakeTimers();
    const id = useToastStore.getState().push({ title: 'hi', durationMs: 0 });
    const { items } = useToastStore.getState();
    expect(id).toMatch(/^t-/);
    expect(items).toHaveLength(1);
    expect(items[0]?.title).toBe('hi');
    expect(items[0]?.id).toBe(id);
  });

  it('dismiss(id) removes the matching item', () => {
    vi.useFakeTimers();
    const a = useToastStore.getState().push({ title: 'first', durationMs: 0 });
    const b = useToastStore.getState().push({ title: 'second', durationMs: 0 });
    expect(useToastStore.getState().items).toHaveLength(2);
    useToastStore.getState().dismiss(a);
    const remaining = useToastStore.getState().items;
    expect(remaining).toHaveLength(1);
    expect(remaining[0]?.id).toBe(b);
  });

  it('durationMs: 0 disables auto-dismiss', () => {
    vi.useFakeTimers();
    useToastStore.getState().push({ title: 'sticky', durationMs: 0 });
    vi.advanceTimersByTime(60_000);
    expect(useToastStore.getState().items).toHaveLength(1);
  });

  it('default durationMs auto-dismisses after 3000ms', () => {
    vi.useFakeTimers();
    useToastStore.getState().push({ title: 'auto' });
    expect(useToastStore.getState().items).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(useToastStore.getState().items).toHaveLength(0);
  });
});

describe('Toaster', () => {
  beforeEach(() => {
    stubRadixPointer();
    useToastStore.setState({ items: [] });
    vi.clearAllTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders queued toast titles', () => {
    vi.useFakeTimers();
    useToastStore.setState({
      items: [
        { id: 'a', title: 'Queued notice', tone: 'neutral' },
        { id: 'b', title: 'Second one', tone: 'success' },
      ],
    });
    render(<Toaster />);
    expect(screen.getByText('Queued notice')).toBeInTheDocument();
    expect(screen.getByText('Second one')).toBeInTheDocument();
  });
});
