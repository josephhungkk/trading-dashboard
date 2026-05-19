import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';

import { renderWithQuery } from '../../../test-utils/render-with-query';
import type { ParamCandidate, ParamSuggestion } from '@/services/param_tuner/types';

import { ParamTunerSection } from './ParamTunerSection';

const mocks = vi.hoisted(() => ({
  listParamSuggestions: vi.fn(),
  triggerParamSuggestion: vi.fn(),
  approveParamSuggestion: vi.fn(),
  rejectParamSuggestion: vi.fn(),
  mintCsrfNonce: vi.fn(),
  useParamTunerStream: vi.fn(),
}));

vi.mock('@/services/param_tuner/api', () => ({
  listParamSuggestions: mocks.listParamSuggestions,
  triggerParamSuggestion: mocks.triggerParamSuggestion,
  approveParamSuggestion: mocks.approveParamSuggestion,
  rejectParamSuggestion: mocks.rejectParamSuggestion,
}));

vi.mock('@/services/admin/api', () => ({
  mintCsrfNonce: mocks.mintCsrfNonce,
}));

vi.mock('../hooks/useParamTunerStream', () => ({
  useParamTunerStream: mocks.useParamTunerStream,
}));

function candidate(index: number): ParamCandidate {
  return {
    params: { fast: index + 5 },
    backtest_job_id: `job-${index}`,
    backtest_result: {
      sharpe: 1.2 + index,
      mar: 0.8,
      max_dd: -3.1,
      win_rate: 0.55,
      avg_trade_pnl: '12.50',
      forced_close_pnl: '0',
      total_trades: 20 + index,
    },
    rank: index + 1,
    delta_vs_current: { sharpe: '+0.20' },
  };
}

function suggestion(status: ParamSuggestion['status']): ParamSuggestion {
  return {
    id: `suggestion-${status}`,
    bot_id: 'bot-1',
    triggered_by: 'manual',
    status,
    candidates: [candidate(0), candidate(1)],
    ai_reasoning: null,
    approved_candidate_index: null,
    created_at: '2026-05-19T12:00:00Z',
    updated_at: '2026-05-19T12:00:00Z',
  };
}

describe('ParamTunerSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listParamSuggestions.mockResolvedValue({ items: [] });
    mocks.mintCsrfNonce.mockResolvedValue('nonce-1');
  });

  it('trigger button visible for admin when no active suggestion', async () => {
    renderWithQuery(<ParamTunerSection botId="bot-1" isAdmin />);
    expect(await screen.findByRole('button', { name: 'Trigger' })).toBeVisible();
  });

  it('backtesting status shown during fan-out', async () => {
    mocks.listParamSuggestions.mockResolvedValue({ items: [suggestion('backtesting')] });
    renderWithQuery(<ParamTunerSection botId="bot-1" isAdmin />);
    expect(await screen.findByRole('status')).toHaveTextContent('Backtesting...');
  });

  it('ranked candidates shown as cards', async () => {
    mocks.listParamSuggestions.mockResolvedValue({ items: [suggestion('ranked')] });
    renderWithQuery(<ParamTunerSection botId="bot-1" isAdmin />);
    expect(await screen.findAllByTestId('param-candidate-card')).toHaveLength(2);
  });

  it('dismiss button shown for failed suggestions', async () => {
    mocks.listParamSuggestions.mockResolvedValue({ items: [suggestion('failed')] });
    renderWithQuery(<ParamTunerSection botId="bot-1" isAdmin />);
    expect(await screen.findByRole('button', { name: 'Dismiss' })).toBeVisible();
  });
});
