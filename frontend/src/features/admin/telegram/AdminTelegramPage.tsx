import * as React from 'react';
import { BotConfigPanel } from './BotConfigPanel';
import { AllowlistPanel } from './AllowlistPanel';
import { CommandLogPanel } from './CommandLogPanel';

export function AdminTelegramPage(): React.JSX.Element {
  return (
    <div className="mx-auto grid max-w-3xl gap-6 p-4">
      <h2 className="text-lg font-semibold text-fg">Telegram Bot</h2>
      <BotConfigPanel />
      <AllowlistPanel />
      <CommandLogPanel />
    </div>
  );
}
