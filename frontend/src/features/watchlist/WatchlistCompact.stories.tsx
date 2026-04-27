import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { WatchlistCompact } from './WatchlistCompact';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';
import { fetchAccountsAndSyncMaintenance } from '@/hooks/useAccountsList';

function Hydrate({
  mode,
  children,
}: {
  mode: 'live' | 'paper';
  children: React.ReactNode;
}): React.JSX.Element {
  useEffect(() => {
    resetServices();
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode, pendingMode: null, status: 'idle' });
    void (mode === 'live' ? live : paper).hydrate(getServices(), fetchAccountsAndSyncMaintenance);
  }, [mode]);
  return <>{children}</>;
}

const meta = {
  title: 'Features/WatchlistCompact',
  component: WatchlistCompact,
  tags: ['autodocs'],
} satisfies Meta<typeof WatchlistCompact>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  decorators: [
    (Story) => (
      <div className="h-[30rem] w-72 bg-bg">
        <Story />
      </div>
    ),
  ],
  render: () => (
    <Hydrate mode="paper">
      <WatchlistCompact />
    </Hydrate>
  ),
};
