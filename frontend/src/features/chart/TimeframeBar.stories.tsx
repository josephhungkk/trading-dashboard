import type { Meta, StoryObj } from '@storybook/react-vite';
import { TimeframeBar } from './TimeframeBar';

const meta = {
  title: 'Features/Chart/TimeframeBar',
  component: TimeframeBar,
  tags: ['autodocs'],
  parameters: {
    layout: 'fullscreen',
  },
} satisfies Meta<typeof TimeframeBar>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Default bar — store initialises at 1m, interval row highlighted accordingly. */
export const Default: Story = {};

/** Shows the full dual-row layout: range presets (desktop only) + interval row. */
export const DualRow: Story = {
  parameters: {
    docs: {
      description: {
        story:
          'Range preset row is hidden below the md breakpoint. Interval row is always visible. ' +
          'Range buttons are display-only until Task 36 fetch-range wiring is implemented.',
      },
    },
    viewport: { defaultViewport: 'desktop' },
  },
};
