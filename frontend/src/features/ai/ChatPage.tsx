import * as React from 'react';

import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';
import { useChatStream } from '@/services/ai/useChatStream';
import type { AICapability, ChatMessage as ChatMessageType } from '@/services/ai/types';
import { useAiStore } from '@/stores/global/ai';

import { ChatMessage } from './ChatMessage';
import { AI_CAPABILITY_OPTIONS, ModelPicker } from './ModelPicker';

const CAPABILITY_VALUES = new Set<AICapability>(
  AI_CAPABILITY_OPTIONS.map((option) => option.value),
);

function asCapability(value: string | null): AICapability {
  if (value !== null && CAPABILITY_VALUES.has(value as AICapability)) {
    return value as AICapability;
  }
  return 'CODING';
}

export function ChatPage(): React.JSX.Element {
  const chatHistory = useAiStore((state) => state.chatHistory);
  const defaultModel = useAiStore((state) => state.defaultModel);
  const appendChatMessage = useAiStore((state) => state.appendChatMessage);
  const setDefaultModel = useAiStore((state) => state.setDefaultModel);
  const chatStream = useChatStream();
  const [capability, setCapability] = React.useState<AICapability>(() =>
    asCapability(defaultModel),
  );
  const [draft, setDraft] = React.useState('');
  const committedPartialRef = React.useRef<string | null>(null);

  const activeStream = chatStream.partial !== '' && !chatStream.done;
  const sendDisabled = activeStream || chatStream.rateLimited || draft.trim() === '';
  const displayedMessages =
    chatStream.partial !== '' && !chatStream.done
      ? [
          ...chatHistory,
          { role: 'assistant', content: chatStream.partial } satisfies ChatMessageType,
        ]
      : chatHistory;

  React.useEffect(() => {
    if (chatStream.partial === '') {
      committedPartialRef.current = null;
      return;
    }
    if (!chatStream.done || committedPartialRef.current === chatStream.partial) return;
    appendChatMessage({ role: 'assistant', content: chatStream.partial });
    committedPartialRef.current = chatStream.partial;
  }, [appendChatMessage, chatStream.done, chatStream.partial]);

  const handleCapabilityChange = (nextCapability: AICapability): void => {
    setCapability(nextCapability);
    setDefaultModel(nextCapability);
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const content = draft.trim();
    if (content === '' || activeStream || chatStream.rateLimited) return;

    const userMessage: ChatMessageType = { role: 'user', content };
    appendChatMessage(userMessage);
    chatStream.send([...chatHistory, userMessage], capability);
    setDraft('');
  };

  return (
    <section className="flex min-h-[calc(100vh-4rem)] flex-col gap-4 p-4">
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold">AI chat</h1>
          <p className="text-sm text-fg-muted">Cost this conversation: recorded in the AI ledger.</p>
        </div>
        <div className="w-full md:w-80">
          <ModelPicker
            value={capability}
            onChange={handleCapabilityChange}
            disabled={activeStream}
          />
        </div>
      </header>

      <div
        className="flex flex-1 flex-col gap-3 overflow-y-auto rounded-md border border-border bg-bg p-3"
        aria-label="Chat history"
      >
        {displayedMessages.length === 0 ? (
          <p className="text-sm text-fg-muted">No messages yet.</p>
        ) : (
          displayedMessages.map((message, index) => (
            <ChatMessage
              key={`${index}-${message.role}-${message.content}`}
              role={message.role}
              content={message.content}
              fallbackBadge={
                index === displayedMessages.length - 1
                && message.role === 'assistant'
                && chatStream.fallbackChain.length > 0
              }
            />
          ))
        )}
      </div>

      {chatStream.error ? (
        <p role="alert" className="text-sm text-destructive">
          {chatStream.error}
        </p>
      ) : null}
      {chatStream.rateLimited ? (
        <p className="text-sm text-warn">wait, max 5/min</p>
      ) : null}

      <form className="flex flex-col gap-2 md:flex-row" onSubmit={handleSubmit}>
        <Input
          aria-label="Message"
          className="min-h-11"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask about risk, sizing, or a trade setup"
          disabled={activeStream}
        />
        <Button type="submit" className="min-h-11 md:w-32" disabled={sendDisabled}>
          Send
        </Button>
      </form>
    </section>
  );
}
