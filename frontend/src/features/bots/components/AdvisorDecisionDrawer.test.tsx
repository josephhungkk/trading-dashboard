import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { userEvent } from '../../../test-utils/render-with-query';
import { AdvisorDecisionDrawer } from './AdvisorDecisionDrawer';
import type { AdvisorDecision } from '../../../services/advisor/types';

const mocks = vi.hoisted(() => ({
  patchAdvisorDecisionOverride: vi.fn(),
  mintCsrfNonce: vi.fn(),
}));

vi.mock('@/services/advisor/api', () => ({
  patchAdvisorDecisionOverride: mocks.patchAdvisorDecisionOverride,
}));

vi.mock('../../../services/admin/api', () => ({
  mintCsrfNonce: mocks.mintCsrfNonce,
}));

function decision(): AdvisorDecision {
  return {
    id: 1,
    bot_id: 'bot-1',
    bot_run_id: null,
    account_id: 'acct-1',
    canonical_id: 'stock:AAPL:US',
    intent: { side: 'buy', qty: '1' },
    context_summary: {
      bar_count: 10,
      position_count: 1,
      recent_fill_count: 0,
      risk_decision_count: 2,
      params_hash: 'abc',
      payload_token_estimate: 100,
    },
    prompt_version: 1,
    verdict: 'veto',
    reasoning: 'Risk is too high.',
    confidence: 0.91,
    advice_tags: ['risk', 'size'],
    provider: 'openai',
    model: 'gpt-test',
    fallback_chain: [],
    latency_ms: 123,
    ai_completion_ts: null,
    ai_completion_request_id: null,
    account_gate_outcome: 'approved',
    account_gate_decision_id: null,
    effective_mode: 'VETO',
    overridden_by: null,
    override_action: null,
    override_reason: null,
    overridden_at: null,
    attribution_status: 'pending',
    outcome_15m_correct: null,
    outcome_15m_pnl: null,
    outcome_1h_correct: null,
    outcome_1h_pnl: null,
    outcome_4h_correct: null,
    outcome_4h_pnl: null,
    outcome_eod_correct: null,
    outcome_eod_pnl: null,
    attribution_computed_at: null,
    created_at: '2026-05-19T12:00:00Z',
  };
}

describe('AdvisorDecisionDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mintCsrfNonce.mockResolvedValue('nonce-1');
    mocks.patchAdvisorDecisionOverride.mockResolvedValue(decision());
  });

  it('renders null when decision is null', () => {
    const { container } = render(
      <AdvisorDecisionDrawer decision={null} isAdmin={false} onClose={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows decision details when open', () => {
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin={false} onClose={vi.fn()} />);
    expect(screen.getByRole('dialog')).toHaveTextContent('stock:AAPL:US');
    expect(screen.getByText('veto')).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByText('123 ms')).toBeInTheDocument();
  });

  it('shows reasoning text', () => {
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin={false} onClose={vi.fn()} />);
    expect(screen.getByText('Risk is too high.')).toBeInTheDocument();
  });

  it('shows advice_tags as badges', () => {
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin={false} onClose={vi.fn()} />);
    expect(screen.getByText('risk')).toBeInTheDocument();
    expect(screen.getByText('size')).toBeInTheDocument();
  });

  it('close button fires onClose', () => {
    const onClose = vi.fn();
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin={false} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape key fires onClose', () => {
    const onClose = vi.fn();
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin={false} onClose={onClose} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('aria-modal is true', () => {
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin={false} onClose={vi.fn()} />);
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  });

  it('shows Override recorded text when overridden_at is set and isAdmin false', () => {
    render(
      <AdvisorDecisionDrawer
        decision={{
          ...decision(),
          overridden_at: '2026-05-19T12:05:00Z',
          overridden_by: 'admin@example.com',
          override_action: 'approve',
          override_reason: 'audit note',
        }}
        isAdmin={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('Override recorded')).toBeInTheDocument();
  });

  it('shows Override (audit only) button when isAdmin true and no override', () => {
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin onClose={vi.fn()} />);
    expect(screen.getByRole('button', { name: /override \(audit only\)/i })).toBeInTheDocument();
  });

  it('hides Override (audit only) button when isAdmin false', () => {
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin={false} onClose={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /override \(audit only\)/i })).not.toBeInTheDocument();
  });

  it('Override button submits PATCH and shows Override recorded for audit purposes', async () => {
    const user = userEvent.setup();
    render(<AdvisorDecisionDrawer decision={decision()} isAdmin onClose={vi.fn()} />);
    await user.type(screen.getByPlaceholderText('Override reason required'), 'manual review');
    await user.click(screen.getByRole('button', { name: /override \(audit only\)/i }));
    await waitFor(() => expect(mocks.patchAdvisorDecisionOverride).toHaveBeenCalledTimes(1));
    expect(mocks.mintCsrfNonce).toHaveBeenCalledTimes(1);
    expect(mocks.patchAdvisorDecisionOverride).toHaveBeenCalledWith(
      'bot-1',
      1,
      { override_action: 'approve', override_reason: 'manual review' },
      'nonce-1',
    );
    expect(
      await screen.findByText(/Override recorded for audit purposes/i),
    ).toBeInTheDocument();
  });
});
