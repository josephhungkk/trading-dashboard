import type { Meta, StoryObj } from '@storybook/react-vite';
import { ResizablePanelFrame } from './ResizablePanelFrame';

const meta = {
  title: 'Patterns/ResizablePanelFrame',
  component: ResizablePanelFrame,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => (
      <div className="h-[30rem]">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ResizablePanelFrame>;

export default meta;
type Story = StoryObj<typeof meta>;

function PanelBody({ label }: { label: string }): React.JSX.Element {
  return <div className="h-full w-full bg-panel p-4 text-sm text-fg">{label}</div>;
}

export const Horizontal3: Story = {
  args: {
    direction: 'horizontal',
    panels: [
      { id: 'left', defaultSize: 20, minSize: 10, content: <PanelBody label="Left" /> },
      { id: 'main', defaultSize: 60, minSize: 30, content: <PanelBody label="Main" /> },
      { id: 'right', defaultSize: 20, minSize: 10, content: <PanelBody label="Right" /> },
    ],
  },
};

export const Vertical2: Story = {
  args: {
    direction: 'vertical',
    panels: [
      { id: 'top', defaultSize: 50, minSize: 20, content: <PanelBody label="Top" /> },
      { id: 'bottom', defaultSize: 50, minSize: 20, content: <PanelBody label="Bottom" /> },
    ],
  },
};

export const CollapsibleLeft: Story = {
  args: {
    direction: 'horizontal',
    panels: [
      {
        id: 'sidebar',
        defaultSize: 20,
        minSize: 10,
        collapsible: true,
        collapsedSize: 3,
        content: <PanelBody label="Sidebar (collapsible)" />,
      },
      { id: 'main', defaultSize: 80, minSize: 30, content: <PanelBody label="Main" /> },
    ],
  },
};
