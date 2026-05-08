import * as React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmDialog } from './ConfirmDialog';
import { mintModifyNonce, submitModify } from './services/orders';

vi.mock('./services/orders', () => ({
  mintModifyNonce: vi.fn(),
  submitModify: vi.fn(),
}));

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason?: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderDialog(
  overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {},
): {
  onCancel: ReturnType<typeof vi.fn>;
  onConfirmed: ReturnType<typeof vi.fn>;
  onError: ReturnType<typeof vi.fn>;
} {
  const onCancel = vi.fn();
  const onConfirmed = vi.fn();
  const onError = vi.fn();
  render(
    <ConfirmDialog
      open
      legId="leg-1"
      type="sl"
      currentPrice={182.5}
      newPrice={184.99}
      tickSize={0.01}
      onCancel={onCancel}
      onConfirmed={onConfirmed}
      onError={onError}
      {...overrides}
    />,
  );
  return { onCancel, onConfirmed, onError };
}

describe('ConfirmDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(mintModifyNonce).mockResolvedValue({
      nonce: 'nonce-1',
      expires_at: '2026-05-08T12:00:30Z',
    });
    vi.mocked(submitModify).mockResolvedValue({ accepted: true });
  });

  it('mints nonce on open and renders Preparing while minting', async () => {
    const mint = deferred<{ nonce: string; expires_at: string }>();
    vi.mocked(mintModifyNonce).mockReturnValue(mint.promise);

    renderDialog();

    // HIGH-3: mintModifyNonce now receives (legId, AbortSignal)
    expect(mintModifyNonce).toHaveBeenCalledWith('leg-1', expect.any(AbortSignal));
    expect(screen.getByText('Preparing…')).toBeInTheDocument();

    mint.resolve({ nonce: 'nonce-1', expires_at: '2026-05-08T12:00:30Z' });
    await waitFor(() => {
      expect(screen.queryByText('Preparing…')).not.toBeInTheDocument();
    });
  });

  it('cancel does not submit modify', async () => {
    const { onCancel } = renderDialog();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(mintModifyNonce).toHaveBeenCalledTimes(1);
    expect(submitModify).not.toHaveBeenCalled();
  });

  it('confirm after mint calls submitModify with nonce and onConfirmed', async () => {
    const { onConfirmed } = renderDialog();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();
    });
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(submitModify).toHaveBeenCalledWith({
      orderId: 'leg-1',
      stopPrice: 184.99,
      nonce: 'nonce-1',
    });
    await waitFor(() => {
      expect(onConfirmed).toHaveBeenCalledWith('nonce-1');
    });
  });

  it('submit returns 412 and calls onError without onConfirmed', async () => {
    vi.mocked(submitModify).mockResolvedValue({
      accepted: false,
      reason: 'nonce_invalid_or_expired',
      status: 412,
    });
    const { onConfirmed, onError } = renderDialog();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();
    });
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith('nonce_invalid_or_expired');
    });
    expect(onConfirmed).not.toHaveBeenCalled();
  });

  it('mint fails and calls onError', async () => {
    vi.mocked(mintModifyNonce).mockRejectedValue(new Error('mint failed'));
    const { onError } = renderDialog();

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith('could not start modify');
    });
  });

  it('displays tick boundary in message', async () => {
    renderDialog({ type: 'tp', currentPrice: 182.5, newPrice: 184.99, tickSize: 0.01 });

    expect(screen.getByText(
      'Move TP from $182.50 to $184.99 (rounded to $0.01 tick)?',
    )).toBeInTheDocument();
  });

  it('confirm button is disabled while minting', () => {
    const mint = deferred<{ nonce: string; expires_at: string }>();
    vi.mocked(mintModifyNonce).mockReturnValue(mint.promise);

    renderDialog();

    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    mint.resolve({ nonce: 'nonce-1', expires_at: '2026-05-08T12:00:30Z' });
  });

  it('aborts in-flight mint on unmount — AbortError caught silently, onError not called', async () => {
    // HIGH-3: unmounting while mint is in-flight must abort the fetch and not call onError.
    const abortMint = deferred<{ nonce: string; expires_at: string }>();
    vi.mocked(mintModifyNonce).mockReturnValue(abortMint.promise);

    const onErrorAbort = vi.fn() as (reason: string) => void;
    const onConfirmedAbort = vi.fn() as (nonce: string) => void;

    const { unmount } = render(
      <ConfirmDialog
        open
        legId="leg-abort"
        type="sl"
        currentPrice={100}
        newPrice={101}
        tickSize={0.01}
        onCancel={vi.fn()}
        onConfirmed={onConfirmedAbort}
        onError={onErrorAbort}
      />,
    );

    // Mint is in-flight; abort signal must have been passed
    expect(mintModifyNonce).toHaveBeenCalledWith('leg-abort', expect.any(AbortSignal));

    // Unmount triggers controller.abort(); reject with AbortError to simulate fetch abort
    await act(async () => {
      unmount();
      const abortErr = new DOMException('aborted', 'AbortError');
      abortMint.reject(abortErr);
      await Promise.resolve();
    });

    // AbortError must be swallowed — onError must NOT be called
    expect(onErrorAbort).not.toHaveBeenCalled();
    expect(onConfirmedAbort).not.toHaveBeenCalled();
  });

  it('confirm button is disabled while submitting with aria-busy true', async () => {
    const submit = deferred<{ accepted: true }>();
    vi.mocked(submitModify).mockReturnValue(submit.promise);
    renderDialog();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();
    });
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Confirm' })).toHaveAttribute('aria-busy', 'true');

    submit.resolve({ accepted: true });
  });
});
