import type { Meta, StoryObj } from '@storybook/react-vite';
import { Switch } from './Switch';

const meta = {
  title: 'Primitives/Switch',
  component: Switch,
  tags: ['autodocs'],
  argTypes: {
    disabled: { control: 'boolean' },
    defaultChecked: { control: 'boolean' },
  },
} satisfies Meta<typeof Switch>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: { 'aria-label': 'toggle' },
};

export const Checked: Story = {
  args: { 'aria-label': 'toggle', defaultChecked: true },
};

export const Disabled: Story = {
  args: { 'aria-label': 'toggle', disabled: true },
};

export const WithLabel: Story = {
  render: (args) => (
    <div className="flex items-center gap-3">
      <Switch id="notifications" {...args} />
      <label htmlFor="notifications" className="text-sm text-fg">
        Enable notifications
      </label>
    </div>
  ),
  args: {},
};
