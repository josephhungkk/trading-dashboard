import { beforeEach, describe, expect, it } from 'vitest';

import { useAiStore } from '@/stores/global/ai';

function resetStore(): void {
  useAiStore.setState({
    chatHistory: [],
    defaultModel: null,
  });
}

describe('useAiStore', () => {
  beforeEach(() => {
    localStorage.clear();
    resetStore();
  });

  it('appends chat messages', () => {
    useAiStore.getState().appendChatMessage({ role: 'user', content: 'hi' });

    expect(useAiStore.getState().chatHistory).toEqual([
      { role: 'user', content: 'hi' },
    ]);
  });

  it('persists default model across rehydrate', async () => {
    useAiStore.getState().setDefaultModel('qwen3-coder');
    const persisted = localStorage.getItem('ai-global');
    if (persisted === null) throw new Error('ai-global was not persisted');
    resetStore();
    localStorage.setItem('ai-global', persisted);

    await useAiStore.persist.rehydrate();

    expect(useAiStore.getState().defaultModel).toBe('qwen3-coder');
  });

  it('migrate guard rejects non-array chat history', async () => {
    localStorage.setItem(
      'ai-global',
      JSON.stringify({
        state: {
          chatHistory: { role: 'user', content: 'bad' },
          defaultModel: ['qwen3-coder'],
        },
        version: 0,
      }),
    );

    await useAiStore.persist.rehydrate();

    expect(useAiStore.getState().chatHistory).toEqual([]);
    expect(useAiStore.getState().defaultModel).toBeNull();
  });

  it('caps chat history at 200 messages with FIFO drop', () => {
    for (let i = 0; i < 201; i += 1) {
      useAiStore.getState().appendChatMessage({
        role: 'user',
        content: `message-${i}`,
      });
    }

    const history = useAiStore.getState().chatHistory;
    expect(history).toHaveLength(200);
    expect(history[0]).toEqual({ role: 'user', content: 'message-1' });
    expect(history[199]).toEqual({ role: 'user', content: 'message-200' });
  });
});
