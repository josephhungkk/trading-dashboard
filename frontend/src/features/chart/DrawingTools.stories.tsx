import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { DrawingTools } from './DrawingTools';
import { useChartStore } from './stores/chartStore';

const meta = {
  title: 'Features/Chart/DrawingTools',
  component: DrawingTools,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
  decorators: [
    (Story) => (
      <div style={{ height: '32rem', display: 'flex' }}>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof DrawingTools>;

export default meta;
type Story = StoryObj<typeof meta>;

function DefaultStory(): React.JSX.Element {
  useEffect(() => {
    useChartStore.setState({ activeDrawingTool: null });
  }, []);
  return <DrawingTools />;
}

function ActiveToolStory(): React.JSX.Element {
  useEffect(() => {
    useChartStore.setState({ activeDrawingTool: 'priceLine' });
    return () => {
      useChartStore.setState({ activeDrawingTool: null });
    };
  }, []);
  return <DrawingTools />;
}

/** Default state — no tool selected. */
export const Default: Story = {
  render: () => <DefaultStory />,
};

/** Active variant — 'priceLine' pre-selected to show highlight style. */
export const ActiveTool: Story = {
  render: () => <ActiveToolStory />,
};
