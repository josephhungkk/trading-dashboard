import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { OpenOrdersCompact } from './OpenOrdersCompact';
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
  title: 'Features/OpenOrdersCompact',
  component: OpenOrdersCompact,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <div className="h-[30rem] w-[22rem]">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof OpenOrdersCompact>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Hydrate mode="paper">
      <OpenOrdersCompact />
    </Hydrate>
  ),
};
