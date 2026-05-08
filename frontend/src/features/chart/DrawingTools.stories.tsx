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
      <div className="flex h-[32rem]">
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

/** Mobile collapse target — priority tools plus More drawings trigger. */
export const MobilePriority: Story = {
  render: () => <DefaultStory />,
  parameters: {
    viewport: {
      defaultViewport: 'mobile1',
    },
  },
};
