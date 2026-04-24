import type { Meta, StoryObj } from '@storybook/react-vite';
import { Badge } from './Badge';

const meta = {
  title: 'Primitives/Badge',
  component: Badge,
  tags: ['autodocs'],
  argTypes: {
    variant: {
      control: 'select',
      options: ['neutral', 'live', 'paper', 'delayed', 'up', 'down', 'warn'],
    },
  },
} satisfies Meta<typeof Badge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Neutral: Story = {
  args: { variant: 'neutral', children: 'neutral' },
};

export const Live: Story = {
  args: { variant: 'live', children: 'live' },
};

export const Paper: Story = {
  args: { variant: 'paper', children: 'paper' },
};

export const Delayed: Story = {
  args: { variant: 'delayed', children: 'delayed 15m' },
};

export const Up: Story = {
  args: { variant: 'up', children: '+1.23%' },
};

export const Down: Story = {
  args: { variant: 'down', children: '-0.45%' },
};

export const Warn: Story = {
  args: { variant: 'warn', children: 'stale' },
};
