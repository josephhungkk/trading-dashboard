import type { Meta, StoryObj } from '@storybook/react-vite';
import { IndicatorPicker } from './IndicatorPicker';

const meta = {
  title: 'Features/Chart/IndicatorPicker',
  component: IndicatorPicker,
  tags: ['autodocs'],
  args: {
    open: true,
    onOpenChange: () => undefined,
  },
  argTypes: {
    open: { control: 'boolean' },
  },
} satisfies Meta<typeof IndicatorPicker>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Picker open on the Technicals tab — default view showing all 27 built-in indicators. */
export const Open: Story = {
  args: { open: true },
};

/** Picker closed — renders nothing (dialog unmounts). */
export const Closed: Story = {
  args: { open: false },
};
