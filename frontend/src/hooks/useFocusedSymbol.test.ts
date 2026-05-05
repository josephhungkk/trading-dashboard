import { renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useFocusedSymbol } from './useFocusedSymbol';

const setFocus = vi.fn();

vi.mock('@/services/registry', () => ({
  getServices: () => ({ quotes: { setFocus } }),
}));

describe('useFocusedSymbol', () => {
  beforeEach(() => {
    setFocus.mockReset();
  });

  it('calls setFocus on mount with the given symbol', () => {
    renderHook(() => useFocusedSymbol('stock:AAPL:US'));
    expect(setFocus).toHaveBeenCalledWith('stock:AAPL:US');
  });

  it('clears focus on unmount', () => {
    const { unmount } = renderHook(() => useFocusedSymbol('stock:AAPL:US'));
    setFocus.mockClear();
    unmount();
    expect(setFocus).toHaveBeenCalledWith(null);
  });

  it('updates focus when the symbol changes', () => {
    const { rerender } = renderHook(({ s }) => useFocusedSymbol(s), {
      initialProps: { s: 'stock:AAPL:US' as string | null },
    });
    setFocus.mockClear();
    rerender({ s: 'stock:GOOG:US' });
    expect(setFocus).toHaveBeenCalledWith(null);
    expect(setFocus).toHaveBeenCalledWith('stock:GOOG:US');
  });

  it('passes null straight through', () => {
    renderHook(() => useFocusedSymbol(null));
    expect(setFocus).toHaveBeenCalledWith(null);
  });
});
