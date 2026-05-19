import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AdvisorDecisionDrawer } from './AdvisorDecisionDrawer';
import type { AdvisorDecision } from '../../../services/advisor/types';

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
    created_at: '2026-05-19T12:00:00Z',
  };
}

describe('AdvisorDecisionDrawer', () => {
  it('renders null when decision is null', () => {
    const { container } = render(<AdvisorDecisionDrawer decision={null} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows decision details when open', () => {
    render(<AdvisorDecisionDrawer decision={decision()} onClose={vi.fn()} />);
    expect(screen.getByRole('dialog')).toHaveTextContent('stock:AAPL:US');
    expect(screen.getByText('veto')).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByText('123 ms')).toBeInTheDocument();
  });

  it('shows reasoning text', () => {
    render(<AdvisorDecisionDrawer decision={decision()} onClose={vi.fn()} />);
    expect(screen.getByText('Risk is too high.')).toBeInTheDocument();
  });

  it('shows advice_tags as badges', () => {
    render(<AdvisorDecisionDrawer decision={decision()} onClose={vi.fn()} />);
    expect(screen.getByText('risk')).toBeInTheDocument();
    expect(screen.getByText('size')).toBeInTheDocument();
  });

  it('close button fires onClose', () => {
    const onClose = vi.fn();
    render(<AdvisorDecisionDrawer decision={decision()} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape key fires onClose', () => {
    const onClose = vi.fn();
    render(<AdvisorDecisionDrawer decision={decision()} onClose={onClose} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('aria-modal is true', () => {
    render(<AdvisorDecisionDrawer decision={decision()} onClose={vi.fn()} />);
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  });
});
