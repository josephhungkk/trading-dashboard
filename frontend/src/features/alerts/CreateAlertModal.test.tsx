import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CreateAlertModal } from '@/features/alerts/CreateAlertModal';
import * as alertsApi from '@/services/alerts/api';

describe('CreateAlertModal', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('submits NL → parses → renders parsed predicate', async () => {
    vi.spyOn(alertsApi, 'postAlert').mockResolvedValue({
      id: 11,
      user_label: 'AAPL > 200',
      original_nl: 'aapl above 200',
      predicate_json: { kind: 'price_threshold', symbol: 'AAPL', op: 'gt', value: 200 },
      requires_capabilities: [],
      parse_status: 'parsed',
      delivery_channels: ['in_app'],
      tick_subscribed: false,
      status: 'pending',
      dormancy_reason: null,
      created_at: '2026-05-13T12:00:00Z',
      updated_at: '2026-05-13T12:00:00Z',
    });

    render(<CreateAlertModal open onClose={vi.fn()} />);

    fireEvent.change(screen.getByTestId('create-alert-label'), {
      target: { value: 'AAPL > 200' },
    });
    fireEvent.change(screen.getByTestId('create-alert-nl'), {
      target: { value: 'aapl above 200' },
    });
    fireEvent.click(screen.getByTestId('create-alert-parse'));

    await waitFor(() => screen.getByTestId('create-alert-parsed'));
    expect(screen.getByTestId('predicate-leaf-price_threshold')).toBeTruthy();
  });

  it('Escape key calls onClose (Codex chunk-D LOW)', () => {
    const onClose = vi.fn();
    render(<CreateAlertModal open onClose={onClose} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('parse_failed response opens ParseFailedEditor', async () => {
    vi.spyOn(alertsApi, 'postAlert').mockResolvedValue({
      id: null,
      parse_status: 'failed',
      partial_predicate: { kind: 'price_threshold' },
      suggestions: ['Specify a symbol', 'Specify a value'],
    });

    render(<CreateAlertModal open onClose={vi.fn()} />);

    fireEvent.change(screen.getByTestId('create-alert-label'), {
      target: { value: 'unparseable' },
    });
    fireEvent.change(screen.getByTestId('create-alert-nl'), {
      target: { value: 'asdf qwer' },
    });
    fireEvent.click(screen.getByTestId('create-alert-parse'));

    await waitFor(() => screen.getByTestId('parse-failed-editor'));
    expect(screen.getByTestId('parse-failed-suggestions').textContent).toMatch(
      /Specify a symbol/,
    );
  });
});
