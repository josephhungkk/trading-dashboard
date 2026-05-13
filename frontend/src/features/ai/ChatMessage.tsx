import * as React from 'react';

import { Badge } from '@/components/primitives/Badge';
import { cn } from '@/lib/utils';
import type { ChatRole } from '@/services/ai/types';

export interface ChatMessageProps {
  role: ChatRole;
  content: string;
  fallbackBadge?: boolean;
}

const ROLE_LABELS: Record<ChatRole, string> = {
  user: 'You',
  assistant: 'Assistant',
  system: 'System',
};

export function ChatMessage({
  role,
  content,
  fallbackBadge = false,
}: ChatMessageProps): React.JSX.Element {
  const isUser = role === 'user';

  return (
    <article
      className={cn(
        'flex w-full',
        isUser ? 'justify-end' : 'justify-start',
      )}
    >
      <div
        className={cn(
          'flex max-w-[min(42rem,88%)] flex-col gap-2 rounded-lg border px-4 py-3 text-sm leading-6',
          isUser
            ? 'border-primary bg-primary text-primary-fg'
            : 'border-border bg-panel text-fg',
          role === 'system' && 'border-warn/40 bg-warn/10',
        )}
      >
        <div
          className={cn(
            'flex min-h-5 flex-wrap items-center gap-2 text-xs font-medium uppercase tracking-wide',
            isUser ? 'text-primary-fg/80' : 'text-fg-muted',
          )}
        >
          <span>{ROLE_LABELS[role]}</span>
          {fallbackBadge ? (
            <Badge variant="warn">Used local fallback (heavy box busy)</Badge>
          ) : null}
        </div>
        <p className="whitespace-pre-wrap break-words">{content}</p>
      </div>
    </article>
  );
}
