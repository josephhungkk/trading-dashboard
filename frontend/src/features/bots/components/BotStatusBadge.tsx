import * as React from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { listBots } from '../../../services/bots/api';
import type { Bot } from '../../../services/bots/types';

export function BotStatusBadge(): React.JSX.Element | null {
  const { data } = useQuery({
    queryKey: ['bots'],
    queryFn: () => listBots(),
    refetchInterval: 10_000,
  });

  const items: Bot[] = data?.items ?? [];
  const running = items.filter((b) => b.status === 'running').length;
  const errors = items.filter((b) => b.status === 'error').length;
  const total = items.length;

  if (total === 0) return null;

  return (
    <span className="text-xs text-muted-foreground">
      {running} running ·{' '}
      {errors > 0 ? (
        <Link to="/bots" search={{ status: 'error' }} className="text-destructive">
          {errors} errors
        </Link>
      ) : (
        '0 errors'
      )}{' '}
      / {total} total
    </span>
  );
}
