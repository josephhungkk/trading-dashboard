import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/features/ai/ChatPage';
import { useChatStream, type UseChatStreamReturn } from '@/services/ai/useChatStream';
import { useAiStore } from '@/stores/global/ai';

vi.mock('@/services/ai/useChatStream', () => ({
  useChatStream: vi.fn(),
}));

const mockedUseChatStream = vi.mocked(useChatStream);

function makeStreamState(
  overrides: Partial<UseChatStreamReturn> = {},
): UseChatStreamReturn {
  return {
    send: vi.fn(),
    partial: '',
    done: false,
    error: null,
    rateLimited: false,
    connected: true,
    fallbackChain: [],
    ...overrides,
  };
}

describe('ChatPage', () => {
  let streamState: UseChatStreamReturn;

  beforeEach(() => {
    localStorage.clear();
    useAiStore.setState({ chatHistory: [], defaultModel: null });
    streamState = makeStreamState();
    mockedUseChatStream.mockImplementation(() => streamState);
  });

  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useAiStore.setState({ chatHistory: [], defaultModel: null });
  });

  it('renders persisted chat history on mount', () => {
    useAiStore.setState({
      chatHistory: [
        { role: 'user', content: 'What is my largest position?' },
        { role: 'assistant', content: 'Your largest position is AAPL.' },
      ],
      defaultModel: 'CODING',
    });

    render(<ChatPage />);

    expect(screen.getByText('What is my largest position?')).toBeInTheDocument();
    expect(screen.getByText('Your largest position is AAPL.')).toBeInTheDocument();
  });

  it('sends a user message immediately and commits the final assistant message', async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    streamState = makeStreamState({ send });
    const { rerender } = render(<ChatPage />);

    await user.type(screen.getByLabelText('Message'), 'hello');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(screen.getByText('hello')).toBeInTheDocument();
    expect(send).toHaveBeenCalledWith([{ role: 'user', content: 'hello' }], 'CODING');

    streamState = makeStreamState({ send, partial: 'hi', done: false });
    rerender(<ChatPage />);
    expect(screen.getByText('hi')).toBeInTheDocument();

    streamState = makeStreamState({ send, partial: 'hi', done: true });
    rerender(<ChatPage />);

    await waitFor(() => {
      expect(useAiStore.getState().chatHistory).toEqual([
        { role: 'user', content: 'hello' },
        { role: 'assistant', content: 'hi' },
      ]);
    });
    expect(screen.getByText('hi')).toBeInTheDocument();
  });

  it('disables send and shows a hint while rate limited', () => {
    streamState = makeStreamState({ rateLimited: true });

    render(<ChatPage />);

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    expect(screen.getByText('wait, max 5/min')).toBeInTheDocument();
  });
});
