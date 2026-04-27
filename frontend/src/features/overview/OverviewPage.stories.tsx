import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { OverviewPage } from './OverviewPage';
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
  title: 'Features/OverviewPage',
  component: OverviewPage,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof OverviewPage>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Hydrate mode="paper">
      <OverviewPage />
    </Hydrate>
  ),
};
