import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import App from './App';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('App', () => {
  it('shows "Backend: ok" when /health returns ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: 'ok', env: 'dev', db: 'ok' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/Backend: ok/)).toBeInTheDocument();
    });
  });

  it('shows "Backend: unreachable" when fetch rejects', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')));
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/Backend: unreachable/)).toBeInTheDocument();
    });
  });
});
