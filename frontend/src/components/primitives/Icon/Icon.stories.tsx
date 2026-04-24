import type { Meta, StoryObj } from '@storybook/react-vite';
import { Bell, Home, Settings } from 'lucide-react';
import { Icon } from './Icon';

const meta = {
  title: 'Primitives/Icon',
  component: Icon,
  tags: ['autodocs'],
  argTypes: {
    size: { control: 'radio', options: ['sm', 'md', 'lg'] },
  },
} satisfies Meta<typeof Icon>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Small: Story = {
  args: { as: Bell, size: 'sm' },
};

export const Medium: Story = {
  args: { as: Home, size: 'md' },
};

export const Large: Story = {
  args: { as: Settings, size: 'lg' },
};

export const WithLabel: Story = {
  args: { as: Bell, size: 'md', 'aria-label': 'notifications' },
};
