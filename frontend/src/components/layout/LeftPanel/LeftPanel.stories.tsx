import type { Meta, StoryObj } from '@storybook/react-vite';
import * as React from 'react';
import { LeftPanel } from './LeftPanel';

function SizedWrapper({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <div className="h-[40rem] w-72 bg-bg">{children}</div>;
}

const meta = {
  title: 'Layout/LeftPanel',
  component: LeftPanel,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof LeftPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <SizedWrapper>
      <LeftPanel />
    </SizedWrapper>
  ),
};
