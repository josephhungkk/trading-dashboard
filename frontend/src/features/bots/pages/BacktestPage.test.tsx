import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { BacktestPage } from './BacktestPage';

vi.mock('@tanstack/react-router', () => ({
  getRouteApi: () => ({
    useParams: () => ({ botId: 'bot-1' }),
  }),
}));

vi.mock('../../../services/backtests/api', () => ({
  submitBacktest: vi.fn(),
  cancelBacktest: vi.fn(),
}));

vi.mock('../hooks/useBacktestStream', () => ({
  useBacktestStream: vi.fn(),
}));

vi.mock('../components/BacktestConfigForm', () => ({
  BacktestConfigForm: ({ onSubmit }: { onSubmit: (c: unknown) => void }) => (
    <button onClick={() => onSubmit({})}>Submit Form</button>
  ),
}));

describe('BacktestPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders config form on initial load', () => {
    render(<BacktestPage />);
    expect(screen.getByRole('button', { name: /submit form/i })).toBeInTheDocument();
  });

  it('switches to running state after submit', async () => {
    const { submitBacktest } = await import('../../../services/backtests/api');
    vi.mocked(submitBacktest).mockResolvedValue({ id: 'job-1' } as never);

    render(<BacktestPage />);
    screen.getByRole('button', { name: /submit form/i }).click();

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /submit form/i })).not.toBeInTheDocument();
    });
  });
});
