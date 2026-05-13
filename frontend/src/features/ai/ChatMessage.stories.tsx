import type { Meta, StoryObj } from '@storybook/react-vite';

import { ChatMessage } from './ChatMessage';
import type { ChatMessageProps } from './ChatMessage';

const meta = {
  title: 'Features/AI/ChatMessage',
  component: ChatMessage,
  tags: ['autodocs'],
} satisfies Meta<typeof ChatMessage>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    role: 'assistant',
    content: 'I can help review your trade setup and identify risk flags.',
  },
};

export const EveryStateVariant: Story = {
  args: {
    role: 'assistant',
    content: 'Every state variant',
  },
  render: () => {
    const messages: ChatMessageProps[] = [
      {
        role: 'system',
        content: 'AI router connected with local fallback available.',
      },
      {
        role: 'user',
        content: 'Summarize the portfolio risk today.',
      },
      {
        role: 'assistant',
        content:
          'Your largest exposure is concentrated in US equities. GBP cash remains sufficient for the planned trade.',
      },
    ];

    return (
      <div className="flex max-w-3xl flex-col gap-4">
        {messages.map((message) => (
          <ChatMessage key={`${message.role}-${message.content}`} {...message} />
        ))}
      </div>
    );
  },
};

export const FallbackBadge: Story = {
  args: {
    role: 'assistant',
    content: 'The heavy model was busy, so I used the local NUC model for this turn.',
    fallbackBadge: true,
  },
};
