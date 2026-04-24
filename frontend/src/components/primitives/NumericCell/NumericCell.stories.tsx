import type { Meta, StoryObj } from '@storybook/react-vite';
import { NumericCell } from './NumericCell';

const meta = {
  title: 'Primitives/NumericCell',
  component: NumericCell,
  tags: ['autodocs'],
  argTypes: {
    format: { control: 'select', options: ['number', 'currency', 'percent'] },
    emphasis: { control: 'select', options: ['up', 'down', 'neutral'] },
  },
} satisfies Meta<typeof NumericCell>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Number: Story = {
  args: { value: 1234.5678, format: 'number', digits: 2 },
};

export const Currency: Story = {
  args: { value: 1234567.89, format: 'currency', currency: 'USD' },
};

export const Percent: Story = {
  args: { value: 0.0523, format: 'percent', digits: 2 },
};

export const EmphasisUp: Story = {
  args: { value: 42.15, format: 'number', emphasis: 'up' },
};

export const EmphasisDown: Story = {
  args: { value: -17.83, format: 'number', emphasis: 'down' },
};

export const Nullish: Story = {
  args: { value: null, format: 'number' },
};
