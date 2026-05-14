import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AdminAiPage } from '@/features/admin/ai/AdminAiPage';

const originalFetch = globalThis.fetch;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function emptyInitialMocks(fetchMock: ReturnType<typeof vi.fn>): void {
  fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url;
    const method = init?.method ?? 'GET';
    if (url.endsWith('/api/admin/config/ai_router/capability_map') && method === 'GET') {
      return Promise.resolve(jsonResponse({
        namespace: 'ai_router',
        key: 'capability_map',
        value: {},
        value_type: 'json',
      }));
    }
    if (url.endsWith('/api/admin/secrets?namespace=ai_provider') && method === 'GET') {
      return Promise.resolve(jsonResponse([]));
    }
    if (url.endsWith('/api/admin/csrf/issue') && method === 'POST') {
      return Promise.resolve(jsonResponse({ nonce: 'abc' }));
    }
    if (url.endsWith('/api/admin/config/ai_router/capability_map') && method === 'PUT') {
      return Promise.resolve(jsonResponse({
        namespace: 'ai_router',
        key: 'capability_map',
        value: { routing: [] },
        value_type: 'json',
      }));
    }
    if (url.endsWith('/api/admin/secrets') && method === 'POST') {
      return Promise.resolve(jsonResponse({
        namespace: 'ai_provider',
        key: 'openai_api_key',
        value_type: 'str',
      }, 201));
    }
    throw new Error(`unhandled fetch: ${method} ${url}`);
  });
}

describe('AdminAiPage', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('renders all 4 sub-panels on mount', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    emptyInitialMocks(fetchMock);

    render(<AdminAiPage />);
    await screen.findByText('Editing ai_router/capability_map');

    expect(screen.getByRole('heading', { name: 'AI router admin' })).toBeInTheDocument();
    expect(screen.getByText('Capability map editor')).toBeInTheDocument();
    expect(screen.getByText('Provider key CRUD')).toBeInTheDocument();
    expect(screen.getAllByText('Cost ledger')).toHaveLength(2);
    expect(screen.getAllByText('Heavy-box state')).toHaveLength(2);
  });

  it('capability map save sends X-Confirm-Nonce', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(globalThis.fetch);
    emptyInitialMocks(fetchMock);

    render(<AdminAiPage />);
    const textarea = await screen.findByLabelText('Capability map JSON');
    fireEvent.change(textarea, { target: { value: '{"routing":[]}' } });
    await user.click(screen.getByRole('button', { name: 'Save capability map' }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        (call) => typeof call[0] === 'string'
          && call[0].endsWith('/api/admin/config/ai_router/capability_map')
          && call[1]?.method === 'PUT',
      );
      expect(putCall).toBeDefined();
      const init = putCall?.[1];
      const headers = new Headers(init?.headers);
      expect(headers.get('X-Confirm-Nonce')).toBe('abc');
    });
  });

  it('provider key add row sends X-Confirm-Nonce', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(globalThis.fetch);
    emptyInitialMocks(fetchMock);

    render(<AdminAiPage />);
    await user.type(screen.getByLabelText('Key name'), 'openai_api_key');
    await user.type(screen.getByLabelText('Secret value'), 'sk-test');
    await user.click(screen.getByRole('button', { name: 'Add provider key' }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => typeof call[0] === 'string'
          && call[0].endsWith('/api/admin/secrets')
          && call[1]?.method === 'POST',
      );
      expect(postCall).toBeDefined();
      const init = postCall?.[1];
      const headers = new Headers(init?.headers);
      expect(headers.get('X-Confirm-Nonce')).toBe('abc');
    });
  });
});
