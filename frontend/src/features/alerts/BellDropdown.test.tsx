import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/useAlertsFeed', () => ({
  useAlertsFeed: () => ({ connected: true, error: null }),
}));

import { BellDropdown } from '@/features/alerts/BellDropdown';
import { useAlertsStore } from '@/stores/global/alerts';

describe('BellDropdown', () => {
  beforeEach(() => {
    useAlertsStore.setState({ recentFires: [], lastSeenAt: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('badge reflects fire count and menu lists fires after toggle', () => {
    useAlertsStore.setState({
      recentFires: [
        { id: 1, alert_id: 7, fired_at: '2026-05-13T12:00:00Z', verdict: 'true' },
        { id: 2, alert_id: 8, fired_at: '2026-05-13T12:01:00Z', verdict: 'true' },
      ],
      lastSeenAt: '2026-05-13T12:01:00Z',
    });

    render(<BellDropdown />);

    expect(screen.getByTestId('bell-badge').textContent).toBe('2');
    fireEvent.click(screen.getByTestId('bell-toggle'));
    expect(screen.getByTestId('bell-fire-1')).toBeTruthy();
    expect(screen.getByTestId('bell-fire-2')).toBeTruthy();
  });

  it('store append reactively bumps the badge', () => {
    render(<BellDropdown />);
    expect(screen.queryByTestId('bell-badge')).toBeNull();

    act(() => {
      useAlertsStore.getState().appendFire({
        id: 99,
        alert_id: 7,
        fired_at: '2026-05-13T14:00:00Z',
        verdict: 'true',
      });
    });

    expect(screen.getByTestId('bell-badge').textContent).toBe('1');
  });
});
