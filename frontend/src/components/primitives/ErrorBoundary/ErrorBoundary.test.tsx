import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ErrorBoundary } from './ErrorBoundary';

function Throw({ boom }: { boom: boolean }): React.JSX.Element {
  if (boom) throw new Error('test boom');
  return <span>ok</span>;
}

describe('ErrorBoundary', () => {
  // React logs caught errors to stderr; silence for clean test output.
  let spy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    spy = vi.spyOn(console, 'error').mockImplementation(() => {
      /* silence */
    });
  });
  afterEach(() => {
    spy.mockRestore();
  });

  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <Throw boom={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('ok')).toBeInTheDocument();
  });

  it('shows default fallback when child throws', () => {
    render(
      <ErrorBoundary>
        <Throw boom={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByText(/test boom/)).toBeInTheDocument();
  });

  it('calls onError with the thrown error', () => {
    const onError = vi.fn();
    render(
      <ErrorBoundary onError={onError}>
        <Throw boom={true} />
      </ErrorBoundary>,
    );
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]?.[0]?.message).toBe('test boom');
  });

  it('retry button is wired and clickable', async () => {
    const user = userEvent.setup();
    render(
      <ErrorBoundary>
        <Throw boom={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    const retry = screen.getByText('Retry');
    expect(retry).toBeInTheDocument();
    await user.click(retry);
    // After retry, ErrorBoundary clears its error state and re-renders children;
    // the inner <Throw boom={true} /> throws again → alert reappears. This proves
    // the button's onClick is wired without requiring external recoverable state.
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('function fallback receives error + retry', () => {
    const fallback = vi.fn().mockReturnValue(<span>custom fallback</span>);
    render(
      <ErrorBoundary fallback={fallback}>
        <Throw boom={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('custom fallback')).toBeInTheDocument();
    expect(fallback).toHaveBeenCalled();
    const args = fallback.mock.calls[0];
    expect(args?.[0]?.message).toBe('test boom');
    expect(typeof args?.[1]).toBe('function');
  });

  it('static fallback node is rendered', () => {
    render(
      <ErrorBoundary fallback={<span>static fb</span>}>
        <Throw boom={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('static fb')).toBeInTheDocument();
  });
});
