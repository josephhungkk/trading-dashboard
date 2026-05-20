import { render, screen } from '@testing-library/react';
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
  Outlet,
} from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { OrchestrationPage } from './OrchestrationPage';

vi.mock('../../services/orchestrator/api', () => ({
  getDigestLatest: vi.fn(),
  getCorrelation: vi.fn(),
  getExposureLimits: vi.fn(),
  getGeneratedStrategies: vi.fn(),
  approveStrategy: vi.fn(),
  rejectStrategy: vi.fn(),
}));

import {
  getDigestLatest,
  getCorrelation,
  getExposureLimits,
  getGeneratedStrategies,
} from '../../services/orchestrator/api';

const mockDigest = getDigestLatest as ReturnType<typeof vi.fn>;
const mockCorrelation = getCorrelation as ReturnType<typeof vi.fn>;
const mockExposure = getExposureLimits as ReturnType<typeof vi.fn>;
const mockStrategyGen = getGeneratedStrategies as ReturnType<typeof vi.fn>;

function renderPage(search = '') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const pageRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/orchestration',
    component: OrchestrationPage,
    validateSearch: (s: Record<string, unknown>) => ({ account_id: s.account_id as string | undefined }),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([pageRoute]),
    history: createMemoryHistory({ initialEntries: [`/orchestration${search}`] }),
  });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

describe('OrchestrationPage', () => {
  beforeEach(() => {
    mockDigest.mockResolvedValue([]);
    mockCorrelation.mockResolvedValue({});
    mockExposure.mockResolvedValue([]);
    mockStrategyGen.mockResolvedValue([]);
  });

  it('renders page heading', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Orchestration' })).toBeInTheDocument();
  });

  it('renders empty league table state', async () => {
    renderPage();
    expect(await screen.findByText('No health data yet')).toBeInTheDocument();
  });

  it('renders bots in league table when data present', async () => {
    mockDigest.mockResolvedValue([
      {
        bot_id: 'bot-1',
        snapshot_at: '2026-05-20T03:00:00Z',
        bot_name: 'Alpha Bot',
        sharpe_30d: '1.25',
        sharpe_7d: '1.40',
        max_drawdown: '0.08',
        win_rate: '0.62',
        total_pnl: null,
        trade_count: 10,
        advisor_veto_accuracy_1h: '0.75',
        exposure_utilisation: null,
        trend_badge: '▲',
      },
      {
        bot_id: 'bot-2',
        snapshot_at: '2026-05-20T03:00:00Z',
        bot_name: 'Beta Bot',
        sharpe_30d: '0.80',
        sharpe_7d: '0.70',
        max_drawdown: '0.15',
        win_rate: '0.50',
        total_pnl: null,
        trade_count: 5,
        advisor_veto_accuracy_1h: null,
        exposure_utilisation: null,
        trend_badge: '▼',
      },
    ]);
    renderPage();
    expect(await screen.findByText('Alpha Bot')).toBeInTheDocument();
    expect(screen.getByText('Beta Bot')).toBeInTheDocument();
  });

  it('shows green ▲ for improving trend badge', async () => {
    mockDigest.mockResolvedValue([
      {
        bot_id: 'bot-1',
        snapshot_at: '2026-05-20T03:00:00Z',
        bot_name: 'Rising Bot',
        sharpe_30d: '1.0',
        sharpe_7d: '1.5',
        max_drawdown: null,
        win_rate: null,
        total_pnl: null,
        trade_count: 3,
        advisor_veto_accuracy_1h: null,
        exposure_utilisation: null,
        trend_badge: '▲',
      },
    ]);
    renderPage();
    const badge = await screen.findByText('▲');
    expect(badge.className).toContain('green');
  });

  it('shows red ▼ for degrading trend badge', async () => {
    mockDigest.mockResolvedValue([
      {
        bot_id: 'bot-1',
        snapshot_at: '2026-05-20T03:00:00Z',
        bot_name: 'Falling Bot',
        sharpe_30d: '1.0',
        sharpe_7d: '0.7',
        max_drawdown: null,
        win_rate: null,
        total_pnl: null,
        trade_count: 2,
        advisor_veto_accuracy_1h: null,
        exposure_utilisation: null,
        trend_badge: '▼',
      },
    ]);
    renderPage();
    const badge = await screen.findByText('▼');
    expect(badge.className).toContain('red');
  });

  it('shows no correlation data when empty matrix', async () => {
    mockCorrelation.mockResolvedValue({});
    renderPage('?account_id=test-acct-id');
    expect(await screen.findByText('No correlation data available')).toBeInTheDocument();
  });

  it('renders empty strategy feed state', async () => {
    renderPage();
    expect(await screen.findByText('No generated strategies yet')).toBeInTheDocument();
  });
});
