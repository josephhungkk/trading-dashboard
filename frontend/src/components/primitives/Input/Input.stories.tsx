import type { Meta, StoryObj } from '@storybook/react-vite';
import { Input } from './Input';

const meta = {
  title: 'Primitives/Input',
  component: Input,
  tags: ['autodocs'],
  argTypes: {
    variant: {
      control: 'select',
      options: ['default', 'numeric'],
    },
    disabled: { control: 'boolean' },
  },
} satisfies Meta<typeof Input>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: { placeholder: 'Enter text...' },
};

export const Numeric: Story = {
  args: { variant: 'numeric', type: 'number', placeholder: '0.00', defaultValue: 42.5 },
};

export const Disabled: Story = {
  args: { placeholder: 'Disabled', disabled: true, defaultValue: 'readonly content' },
};

export const WithError: Story = {
  args: {
    placeholder: 'Email',
    'aria-invalid': true,
    className: 'border-destructive focus-visible:ring-destructive',
    defaultValue: 'bad@',
  },
};
