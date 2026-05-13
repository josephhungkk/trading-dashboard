import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AlertsPage } from '@/features/alerts/AlertsPage';
import * as alertsApi from '@/services/alerts/api';
import type { AlertRule } from '@/services/alerts/types';

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}

function makeRule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: 1,
    user_label: 'AAPL > 200',
    original_nl: 'alert me when AAPL crosses 200',
    predicate_json: { kind: 'price_threshold', symbol: 'AAPL', op: 'gt', value: 200 },
    requires_capabilities: [],
    parse_status: 'manual',
    delivery_channels: ['in_app'],
    tick_subscribed: false,
    status: 'active',
    dormancy_reason: null,
    created_at: '2026-05-13T12:00:00Z',
    updated_at: '2026-05-13T12:00:00Z',
    ...overrides,
  };
}

describe('AlertsPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('filters by tab — dormant tab hides active rules', async () => {
    vi.spyOn(alertsApi, 'listAlerts').mockResolvedValue({
      alerts: [
        makeRule({ id: 1, status: 'active', user_label: 'A1' }),
        makeRule({ id: 2, status: 'dormant', user_label: 'D2' }),
      ],
    });

    render(<AlertsPage />, { wrapper: makeWrapper() });

    await waitFor(() => screen.getByTestId('alerts-row-1'));
    expect(screen.queryByTestId('alerts-row-2')).toBeNull();

    fireEvent.click(screen.getByTestId('alerts-tab-dormant'));
    await waitFor(() => screen.getByTestId('alerts-row-2'));
    expect(screen.queryByTestId('alerts-row-1')).toBeNull();
  });

  it('delete button calls deleteAlert and refreshes list', async () => {
    const listMock = vi
      .spyOn(alertsApi, 'listAlerts')
      .mockResolvedValueOnce({ alerts: [makeRule({ id: 7 })] })
      .mockResolvedValueOnce({ alerts: [] });
    const deleteMock = vi.spyOn(alertsApi, 'deleteAlert').mockResolvedValue();

    render(<AlertsPage />, { wrapper: makeWrapper() });
    await waitFor(() => screen.getByTestId('alerts-row-7'));

    fireEvent.click(screen.getByTestId('alerts-delete-7'));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith(7));
    await waitFor(() => screen.getByTestId('alerts-empty-active'));
    expect(listMock).toHaveBeenCalledTimes(2);
  });
});
