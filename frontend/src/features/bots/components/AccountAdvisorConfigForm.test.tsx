import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { userEvent } from '../../../test-utils/render-with-query';
import { AccountAdvisorConfigForm } from './AccountAdvisorConfigForm';

const mocks = vi.hoisted(() => ({
  putAccountAdvisorConfig: vi.fn(),
  mintCsrfNonce: vi.fn(),
}));

vi.mock('@/services/advisor/api', () => ({
  putAccountAdvisorConfig: mocks.putAccountAdvisorConfig,
}));

vi.mock('../../../services/admin/api', () => ({
  mintCsrfNonce: mocks.mintCsrfNonce,
}));

describe('AccountAdvisorConfigForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mintCsrfNonce.mockResolvedValue('nonce-1');
    mocks.putAccountAdvisorConfig.mockResolvedValue({
      bot_id: 'bot-1',
      account_id: 'acct-1',
      action: 'clear',
    });
  });

  it('shows Using bot default when advisor_config_override null', () => {
    render(
      <AccountAdvisorConfigForm
        botId="bot-1"
        account={{ account_id: 'acct-1', advisor_config_override: null }}
        botConfig={{ mode: 'OBSERVE', local_only: false }}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByText('Using bot default')).toBeInTheDocument();
  });

  it('renders Mode select when override non-null', () => {
    render(
      <AccountAdvisorConfigForm
        botId="bot-1"
        account={{ account_id: 'acct-1', advisor_config_override: { mode: 'VETO' } }}
        botConfig={{ mode: 'OBSERVE', local_only: false }}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/mode/i)).toBeInTheDocument();
  });

  it('Clear override button calls putAccountAdvisorConfig with null body', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    render(
      <AccountAdvisorConfigForm
        botId="bot-1"
        account={{ account_id: 'acct-1', advisor_config_override: { mode: 'VETO' } }}
        botConfig={{ mode: 'OBSERVE', local_only: false }}
        onSaved={onSaved}
      />,
    );
    await user.click(screen.getByRole('button', { name: /clear override/i }));
    await waitFor(() => expect(mocks.putAccountAdvisorConfig).toHaveBeenCalledTimes(1));
    expect(mocks.putAccountAdvisorConfig).toHaveBeenCalledWith(
      'bot-1',
      'acct-1',
      { advisor_config_override: null },
      'nonce-1',
    );
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('does not render disallowed concurrency field', () => {
    render(
      <AccountAdvisorConfigForm
        botId="bot-1"
        account={{ account_id: 'acct-1', advisor_config_override: null }}
        botConfig={{ mode: 'OBSERVE', local_only: false }}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.queryByText(/max[_ ]concurrent/i)).not.toBeInTheDocument();
  });
});
