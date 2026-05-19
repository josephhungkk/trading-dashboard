import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithQuery, userEvent } from '../../../test-utils/render-with-query';
import { AdvisorDecisionsTable } from './AdvisorDecisionsTable';
import type { AdvisorDecision, AdvisorDecisionsPage, AdvisorVerdict } from '../../../services/advisor/types';

const mocks = vi.hoisted(() => ({
  getAdvisorDecisions: vi.fn(),
}));

vi.mock('../../../services/advisor/api', () => ({
  getAdvisorDecisions: mocks.getAdvisorDecisions,
}));

function decision(id: number, verdict: AdvisorVerdict): AdvisorDecision {
  return {
    id,
    bot_id: 'bot-1',
    bot_run_id: null,
    account_id: 'acct-1',
    canonical_id: `stock:TEST${id}:US`,
    intent: { side: 'buy' },
    context_summary: {
      bar_count: 1,
      position_count: 0,
      recent_fill_count: 0,
      risk_decision_count: 0,
      params_hash: 'hash',
      payload_token_estimate: 10,
    },
    prompt_version: 1,
    verdict,
    reasoning: `reason ${id}`,
    confidence: id === 3 ? null : 0.8,
    advice_tags: ['tag'],
    provider: 'openai',
    model: 'gpt-test',
    fallback_chain: [],
    latency_ms: 50 + id,
    ai_completion_ts: null,
    ai_completion_request_id: null,
    account_gate_outcome: 'approved',
    account_gate_decision_id: null,
    effective_mode: 'VETO',
    overridden_by: null,
    override_action: null,
    override_reason: null,
    overridden_at: null,
    created_at: '2026-05-19T12:00:00Z',
  };
}

function page(items: AdvisorDecision[], nextCursor: string | null = null): AdvisorDecisionsPage {
  return { items, next_cursor: nextCursor };
}

describe('AdvisorDecisionsTable', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getAdvisorDecisions.mockResolvedValue(page([]));
  });

  it('renders empty state', async () => {
    renderWithQuery(<AdvisorDecisionsTable botId="bot-1" />);
    expect(await screen.findByText(/no advisor decisions yet/i)).toBeInTheDocument();
  });

  it('renders rows from data', async () => {
    mocks.getAdvisorDecisions.mockResolvedValue(page([decision(1, 'approve')]));
    renderWithQuery(<AdvisorDecisionsTable botId="bot-1" />);
    expect(await screen.findByText('stock:TEST1:US')).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
    expect(screen.getByText('51 ms')).toBeInTheDocument();
  });

  it('verdict badge colored correctly', async () => {
    mocks.getAdvisorDecisions.mockResolvedValue(
      page([decision(1, 'approve'), decision(2, 'veto'), decision(3, 'fail_open')]),
    );
    renderWithQuery(<AdvisorDecisionsTable botId="bot-1" />);
    expect(await screen.findByText('approve')).toHaveClass('bg-green-100');
    expect(screen.getByText('veto')).toHaveClass('bg-red-100');
    expect(screen.getByText('fail_open')).toHaveClass('bg-yellow-100');
  });

  it('shows Overridden badge when overridden_at is set', async () => {
    mocks.getAdvisorDecisions.mockResolvedValue(
      page([
        {
          ...decision(1, 'approve'),
          overridden_at: '2026-05-19T12:05:00Z',
          override_action: 'approve',
          overridden_by: 'admin@example.com',
          override_reason: 'audit note',
        },
      ]),
    );
    renderWithQuery(<AdvisorDecisionsTable botId="bot-1" />);
    expect(await screen.findByText('Overridden')).toBeInTheDocument();
  });

  it('shows Load more button when next_before present', async () => {
    mocks.getAdvisorDecisions.mockResolvedValue(page([decision(1, 'approve')], 'cursor-1'));
    renderWithQuery(<AdvisorDecisionsTable botId="bot-1" />);
    expect(await screen.findByRole('button', { name: /load more/i })).toBeInTheDocument();
  });

  it('Load more button calls next page', async () => {
    mocks.getAdvisorDecisions
      .mockResolvedValueOnce(page([decision(1, 'approve')], '2026-05-19T12:00:00Z'))
      .mockResolvedValueOnce(page([decision(2, 'veto')]));
    const user = userEvent.setup();
    renderWithQuery(<AdvisorDecisionsTable botId="bot-1" />);
    await user.click(await screen.findByRole('button', { name: /load more/i }));
    await waitFor(() =>
      expect(mocks.getAdvisorDecisions).toHaveBeenLastCalledWith('bot-1', {
        before: '2026-05-19T12:00:00Z',
      }),
    );
  });

  it('row click opens drawer', async () => {
    mocks.getAdvisorDecisions.mockResolvedValue(page([decision(1, 'approve')]));
    const user = userEvent.setup();
    renderWithQuery(<AdvisorDecisionsTable botId="bot-1" />);
    await user.click(await screen.findByText('stock:TEST1:US'));
    expect(screen.getByRole('dialog')).toHaveTextContent('reason 1');
  });
});
