import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AdvisorFeedPage } from './AdvisorFeedPage';
import type { AdvisorWsFrame } from '../../../services/advisor/types';

const mocks = vi.hoisted(() => ({
  useAdvisorFeedStream: vi.fn(),
}));

vi.mock('../hooks/useAdvisorFeedStream', () => ({
  useAdvisorFeedStream: mocks.useAdvisorFeedStream,
}));

function frame(decisionId: number, verdict: AdvisorWsFrame['verdict']): AdvisorWsFrame {
  return {
    v: 1,
    type: 'decision',
    decision_id: decisionId,
    bot_id: `bot-${decisionId}`,
    canonical_id: `stock:TEST${decisionId}:US`,
    verdict,
    reasoning_preview: `reason ${decisionId}`,
    latency_ms: 50,
    effective_mode: 'OBSERVE',
    ts: '2026-05-19T12:00:00Z',
  };
}

describe('AdvisorFeedPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useAdvisorFeedStream.mockReturnValue({ frames: [], isConnected: true });
  });

  it('renders connected badge when isConnected=true', () => {
    render(<AdvisorFeedPage />);
    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  it('renders disconnected badge when isConnected=false', () => {
    mocks.useAdvisorFeedStream.mockReturnValue({ frames: [], isConnected: false });
    render(<AdvisorFeedPage />);
    expect(screen.getByText('Disconnected')).toBeInTheDocument();
  });

  it('renders rows from frames', () => {
    mocks.useAdvisorFeedStream.mockReturnValue({
      frames: [frame(1, 'approve')],
      isConnected: true,
    });
    render(<AdvisorFeedPage />);
    expect(screen.getByText('bot-1')).toBeInTheDocument();
    expect(screen.getByText('stock:TEST1:US')).toBeInTheDocument();
    expect(screen.getByText('reason 1')).toBeInTheDocument();
  });

  it('verdict filter works', async () => {
    const user = userEvent.setup();
    mocks.useAdvisorFeedStream.mockReturnValue({
      frames: [frame(1, 'approve'), frame(2, 'veto')],
      isConnected: true,
    });
    render(<AdvisorFeedPage />);
    await user.selectOptions(screen.getByLabelText(/verdict filter/i), 'veto');
    expect(screen.queryByText('stock:TEST1:US')).not.toBeInTheDocument();
    expect(screen.getByText('stock:TEST2:US')).toBeInTheDocument();
  });
});
