import type { Meta, StoryObj } from '@storybook/react-vite';
import { Checkbox } from './Checkbox';

const meta = {
  title: 'Primitives/Checkbox',
  component: Checkbox,
  tags: ['autodocs'],
  argTypes: {
    disabled: { control: 'boolean' },
    defaultChecked: { control: 'boolean' },
  },
} satisfies Meta<typeof Checkbox>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: { 'aria-label': 'accept' },
};

export const Checked: Story = {
  args: { 'aria-label': 'accept', defaultChecked: true },
};

export const Disabled: Story = {
  args: { 'aria-label': 'accept', disabled: true },
};

export const WithLabel: Story = {
  render: (args) => (
    <div className="flex items-center gap-2">
      <Checkbox id="terms" {...args} />
      <label htmlFor="terms" className="text-sm text-fg">
        I accept the terms
      </label>
    </div>
  ),
  args: {},
};
