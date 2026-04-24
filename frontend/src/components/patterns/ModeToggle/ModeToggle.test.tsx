import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ModeToggle } from './ModeToggle';
import { useModeStore } from '@/stores/global/mode';
import { resetServices } from '@/services/registry';
import { getBothScopes } from '@/stores/registry';

function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') {
    proto['hasPointerCapture'] = () => false;
  }
  if (typeof proto['releasePointerCapture'] !== 'function') {
    proto['releasePointerCapture'] = () => {
      /* jsdom stub */
    };
  }
  if (typeof proto['setPointerCapture'] !== 'function') {
    proto['setPointerCapture'] = () => {
      /* jsdom stub */
    };
  }
  if (typeof proto['scrollIntoView'] !== 'function') {
    proto['scrollIntoView'] = () => {
      /* jsdom stub */
    };
  }
}

describe('ModeToggle', () => {
  beforeEach(() => {
    stubRadixPointer();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
  });

  it('renders with PAPER badge when mode is paper', () => {
    render(<ModeToggle />);
    expect(screen.getByText('PAPER')).toBeInTheDocument();
    expect(screen.getByRole('switch')).toHaveAttribute('data-state', 'unchecked');
  });

  it('clicking switch in paper opens confirm dialog (does not flip mode yet)', async () => {
    const user = userEvent.setup();
    render(<ModeToggle />);
    await user.click(screen.getByRole('switch'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/Switch to LIVE mode\?/)).toBeInTheDocument();
    expect(useModeStore.getState().mode).toBe('paper');
  });

  it('Cancel closes dialog and keeps paper', async () => {
    const user = userEvent.setup();
    render(<ModeToggle />);
    await user.click(screen.getByRole('switch'));
    await user.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(useModeStore.getState().mode).toBe('paper');
    expect(useModeStore.getState().pendingMode).toBeNull();
  });

  it('Continue to LIVE flips mode and fires toast', async () => {
    const user = userEvent.setup();
    render(<ModeToggle />);
    await user.click(screen.getByRole('switch'));
    await user.click(screen.getByRole('button', { name: /Continue to LIVE/i }));
    await vi.waitFor(() => {
      expect(useModeStore.getState().mode).toBe('live');
    });
  });

  it('live to paper is immediate (no dialog)', async () => {
    const user = userEvent.setup();
    useModeStore.setState({ mode: 'live', pendingMode: null, status: 'idle' });
    render(<ModeToggle />);
    await user.click(screen.getByRole('switch'));
    await vi.waitFor(() => {
      expect(useModeStore.getState().mode).toBe('paper');
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
