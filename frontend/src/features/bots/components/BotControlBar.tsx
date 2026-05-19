import * as React from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  deployBot,
  pauseBot,
  resumeBot,
  startBot,
  stopBot,
} from '../../../services/bots/api';
import type { Bot } from '../../../services/bots/types';

interface Props {
  bot: Bot;
}

export function BotControlBar({ bot }: Props): React.JSX.Element {
  const qc = useQueryClient();
  const [confirmLive, setConfirmLive] = React.useState(false);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['bots'] });
    void qc.invalidateQueries({ queryKey: ['bot', bot.id] });
  };

  const startMut = useMutation({ mutationFn: () => startBot(bot.id), onSuccess: invalidate });
  const stopMut = useMutation({ mutationFn: () => stopBot(bot.id), onSuccess: invalidate });
  const pauseMut = useMutation({ mutationFn: () => pauseBot(bot.id), onSuccess: invalidate });
  const resumeMut = useMutation({ mutationFn: () => resumeBot(bot.id), onSuccess: invalidate });
  const deployMut = useMutation({ mutationFn: () => deployBot(bot.id), onSuccess: invalidate });

  const handleStart = () => {
    if (bot.mode === 'live' && !confirmLive) {
      setConfirmLive(true);
      return;
    }
    setConfirmLive(false);
    startMut.mutate();
  };

  return (
    <div className="flex items-center gap-2">
      {confirmLive && (
        <span className="text-sm text-destructive">
          Starting in LIVE mode. Click Start again to confirm.
        </span>
      )}
      {bot.status === 'stopped' && (
        <button onClick={handleStart} className="btn-primary">
          Start
        </button>
      )}
      {bot.status === 'running' && (
        <>
          <button onClick={() => pauseMut.mutate()} className="btn-secondary">
            Pause
          </button>
          <button onClick={() => stopMut.mutate()} className="btn-destructive">
            Stop
          </button>
          <button onClick={() => deployMut.mutate()} className="btn-secondary">
            Deploy
          </button>
        </>
      )}
      {bot.status === 'paused' && (
        <>
          <button onClick={() => resumeMut.mutate()} className="btn-primary">
            Resume
          </button>
          <button onClick={() => stopMut.mutate()} className="btn-destructive">
            Stop
          </button>
        </>
      )}
    </div>
  );
}
