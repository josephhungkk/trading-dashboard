import type { Meta, StoryObj } from '@storybook/react-vite';
import { ChartToolbar } from './ChartToolbar';

const meta = {
  title: 'Features/Chart/ChartToolbar',
  component: ChartToolbar,
  tags: ['autodocs'],
  parameters: {
    layout: 'fullscreen',
  },
} satisfies Meta<typeof ChartToolbar>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Default toolbar state — candle chart type, no open modals. */
export const Default: Story = {};

/** Toolbar with screenshot button visually disabled (always-on placeholder). */
export const ScreenshotDisabled: Story = {
  parameters: {
    docs: {
      description: {
        story:
          'Screenshot action is a deferred v0.9.1 placeholder. The button renders disabled with a "coming soon" tooltip.',
      },
    },
  },
};
