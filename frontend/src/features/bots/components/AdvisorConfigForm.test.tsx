import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithQuery, userEvent } from '../../../test-utils/render-with-query';
import { AdvisorConfigForm } from './AdvisorConfigForm';
import type { AdvisorConfigResponse } from '../../../services/advisor/types';

const mocks = vi.hoisted(() => ({
  getAdvisorConfig: vi.fn(),
  updateAdvisorConfig: vi.fn(),
  mintCsrfNonce: vi.fn(),
  calls: [] as string[],
}));

vi.mock('../../../services/advisor/api', () => ({
  getAdvisorConfig: mocks.getAdvisorConfig,
  updateAdvisorConfig: mocks.updateAdvisorConfig,
}));

vi.mock('../../../services/admin/api', () => ({
  mintCsrfNonce: mocks.mintCsrfNonce,
}));

function response(): AdvisorConfigResponse {
  return {
    bot_id: 'bot-1',
    account_overrides: {},
    config: {
      mode: 'OBSERVE',
      capability: 'reasoning',
      local_only: false,
      timeout_ms: 2500,
      daily_budget_usd: '7.50',
      max_qps: 2,
      auto_pause_threshold: 3,
      auto_pause_window_seconds: 300,
      min_veto_confidence: 0.65,
    },
  };
}

describe('AdvisorConfigForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.calls.length = 0;
    mocks.getAdvisorConfig.mockResolvedValue(response());
    mocks.mintCsrfNonce.mockImplementation(async () => {
      mocks.calls.push('csrf');
      return 'nonce-1';
    });
    mocks.updateAdvisorConfig.mockImplementation(async () => {
      mocks.calls.push('put');
      return response();
    });
  });

  it('renders form fields', async () => {
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    expect(await screen.findByLabelText(/mode/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/timeout ms/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/daily budget usd/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/auto pause threshold/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/min veto confidence/i)).toBeInTheDocument();
  });

  it('displays current config values', async () => {
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    expect(await screen.findByDisplayValue('OBSERVE')).toBeInTheDocument();
    expect(screen.getByDisplayValue('2500')).toBeInTheDocument();
    expect(screen.getByDisplayValue('7.50')).toBeInTheDocument();
    expect(screen.getByDisplayValue('3')).toBeInTheDocument();
    expect(screen.getByDisplayValue('0.65')).toBeInTheDocument();
  });

  it('validates min_veto_confidence range 0-1', async () => {
    const user = userEvent.setup();
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    const input = await screen.findByLabelText(/min veto confidence/i);
    await user.clear(input);
    await user.type(input, '1.25');
    await user.click(screen.getByRole('button', { name: /save advisor config/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/between 0 and 1/i);
    expect(mocks.updateAdvisorConfig).not.toHaveBeenCalled();
  });

  it('calls updateAdvisorConfig on submit', async () => {
    const user = userEvent.setup();
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    await screen.findByDisplayValue('OBSERVE');
    await user.click(screen.getByRole('button', { name: /save advisor config/i }));
    await waitFor(() => expect(mocks.updateAdvisorConfig).toHaveBeenCalledTimes(1));
    expect(mocks.updateAdvisorConfig).toHaveBeenCalledWith(
      'bot-1',
      expect.objectContaining({ mode: 'OBSERVE', timeout_ms: 2500 }),
      'nonce-1',
    );
  });

  it('shows success state after save', async () => {
    const user = userEvent.setup();
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    await screen.findByDisplayValue('OBSERVE');
    await user.click(screen.getByRole('button', { name: /save advisor config/i }));
    expect(await screen.findByRole('button', { name: /saved/i })).toBeInTheDocument();
  });

  it('shows error state on API failure', async () => {
    mocks.updateAdvisorConfig.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    await screen.findByDisplayValue('OBSERVE');
    await user.click(screen.getByRole('button', { name: /save advisor config/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
  });

  it('mode select has OFF/OBSERVE/VETO options', async () => {
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    const select = await screen.findByLabelText(/mode/i);
    expect(select).toHaveTextContent('OFF');
    expect(select).toHaveTextContent('OBSERVE');
    expect(select).toHaveTextContent('VETO');
  });

  it('CSRF nonce is minted before PUT call', async () => {
    const user = userEvent.setup();
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    await screen.findByDisplayValue('OBSERVE');
    await user.click(screen.getByRole('button', { name: /save advisor config/i }));
    await waitFor(() => expect(mocks.calls).toEqual(['csrf', 'put']));
  });

  it('submit button disabled during loading', async () => {
    mocks.updateAdvisorConfig.mockImplementation(
      () => new Promise<AdvisorConfigResponse>(() => undefined),
    );
    const user = userEvent.setup();
    renderWithQuery(<AdvisorConfigForm botId="bot-1" />);
    await screen.findByDisplayValue('OBSERVE');
    await user.click(screen.getByRole('button', { name: /save advisor config/i }));
    expect(await screen.findByRole('button', { name: /saving/i })).toBeDisabled();
  });
});
