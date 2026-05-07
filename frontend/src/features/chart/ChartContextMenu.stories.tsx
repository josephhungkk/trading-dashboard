import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { ChartContextMenu } from './ChartContextMenu';
import { useChartStore } from './stores/chartStore';

const meta = {
  title: 'Features/Chart/ChartContextMenu',
  component: ChartContextMenu,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
  args: {
    position: { x: 160, y: 120 },
    onClose: () => undefined,
    onAddIndicator: () => undefined,
    onCopySnapshot: async () => undefined,
  },
} satisfies Meta<typeof ChartContextMenu>;

export default meta;
type Story = StoryObj<typeof meta>;

function OpenStory(args: React.ComponentProps<typeof ChartContextMenu>): React.JSX.Element {
  useEffect(() => {
    useChartStore.setState({ indicators: [] });
  }, []);
  return <ChartContextMenu {...args} />;
}

function WithIndicatorsStory(args: React.ComponentProps<typeof ChartContextMenu>): React.JSX.Element {
  useEffect(() => {
    useChartStore.setState({ indicators: ['MA', 'RSI'] });
    return () => {
      useChartStore.setState({ indicators: [] });
    };
  }, []);
  return <ChartContextMenu {...args} />;
}

/** Menu is closed — renders nothing. */
export const Closed: Story = {
  args: { open: false },
};

/** Menu is open, no active indicators. */
export const Open: Story = {
  args: { open: true },
  render: (args) => <OpenStory {...args} />,
};

/** Menu is open with two active indicators — shows "Remove Indicator" entry. */
export const WithIndicators: Story = {
  args: { open: true },
  render: (args) => <WithIndicatorsStory {...args} />,
};
