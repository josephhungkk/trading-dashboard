/**
 * Phase 11a-D — persisted AI chat UI state.
 * Matches the portfolio store's migrate-guard shape so corrupted localStorage
 * cannot hydrate non-string model values or malformed chat messages.
 */

import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import type { ChatMessage, ChatRole } from '@/services/ai/types';

const MAX_CHAT_HISTORY = 200;
const SUPPORTED_ROLES: ReadonlySet<ChatRole> = new Set([
  'user',
  'assistant',
  'system',
]);

interface AiStore {
  chatHistory: ChatMessage[];
  defaultModel: string | null;
  appendChatMessage: (message: ChatMessage) => void;
  setDefaultModel: (model: string | null) => void;
  clearChatHistory: () => void;
}

const initialState = {
  chatHistory: [],
  defaultModel: null,
} satisfies Pick<AiStore, 'chatHistory' | 'defaultModel'>;

function isChatMessage(value: unknown): value is ChatMessage {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }
  const candidate = value as { role?: unknown; content?: unknown };
  return (
    typeof candidate.role === 'string'
    && SUPPORTED_ROLES.has(candidate.role as ChatRole)
    && typeof candidate.content === 'string'
  );
}

function capHistory(messages: ChatMessage[]): ChatMessage[] {
  return messages.slice(-MAX_CHAT_HISTORY);
}

export const useAiStore = create<AiStore>()(
  persist(
    (set) => ({
      ...initialState,
      appendChatMessage: (message: ChatMessage) =>
        set((state) => ({
          chatHistory: capHistory([...state.chatHistory, message]),
        })),
      setDefaultModel: (model: string | null) => set({ defaultModel: model }),
      clearChatHistory: () => set({ chatHistory: [] }),
    }),
    {
      name: 'ai-global',
      storage: createJSONStorage(() => localStorage),
      version: 1,
      migrate: (state: unknown) => {
        const s = state as {
          chatHistory?: unknown;
          defaultModel?: unknown;
        } | null;
        const persistedHistory = s?.chatHistory;
        const persistedDefaultModel = s?.defaultModel;
        const chatHistory = Array.isArray(persistedHistory)
          ? capHistory(persistedHistory.filter(isChatMessage))
          : [];
        if (Array.isArray(persistedHistory)) {
          const droppedCount = persistedHistory.length - chatHistory.length;
          if (droppedCount > 0) {
            console.warn(`[ai-store] migrate dropped ${droppedCount} invalid chat messages`);
          }
        }
        // Security: explicit string-or-null typed check before hydrating.
        // This mirrors the portfolio migrate guard's defensive shape.
        const defaultModel =
          typeof persistedDefaultModel === 'string' || persistedDefaultModel === null
            ? persistedDefaultModel
            : null;
        if (
          persistedDefaultModel !== undefined
          && persistedDefaultModel !== null
          && typeof persistedDefaultModel !== 'string'
        ) {
          console.warn('[ai-store] migrate coerced invalid defaultModel to null');
        }
        return { chatHistory, defaultModel };
      },
    },
  ),
);
