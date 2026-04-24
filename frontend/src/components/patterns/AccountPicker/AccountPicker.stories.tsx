import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { AccountPicker } from './AccountPicker';
import { useModeStore } from '@/stores/global/mode';
import { getBothScopes } from '@/stores/registry';
import { getServices, resetServices } from '@/services/registry';

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
    void (mode === 'live' ? live : paper).hydrate(getServices());
  }, [mode]);
  return <>{children}</>;
}

const meta = {
  title: 'Patterns/AccountPicker',
  component: AccountPicker,
  tags: ['autodocs'],
} satisfies Meta<typeof AccountPicker>;

export default meta;
type Story = StoryObj<typeof meta>;

export const PaperAccounts: Story = {
  render: () => (
    <Hydrate mode="paper">
      <AccountPicker />
    </Hydrate>
  ),
};

export const LiveAccounts: Story = {
  render: () => (
    <Hydrate mode="live">
      <AccountPicker />
    </Hydrate>
  ),
};

export const Empty: Story = {
  render: () => {
    const { live, paper } = getBothScopes();
    live.suspend();
    paper.suspend();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    return <AccountPicker />;
  },
};
