import type { Meta, StoryObj } from '@storybook/react-vite';
import { RadioGroup, RadioItem } from './Radio';

const meta = {
  title: 'Primitives/Radio',
  component: RadioGroup,
  tags: ['autodocs'],
  argTypes: {
    disabled: { control: 'boolean' },
  },
} satisfies Meta<typeof RadioGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: (args) => (
    <RadioGroup defaultValue="medium" aria-label="size" {...args}>
      <div className="flex items-center gap-2">
        <RadioItem value="small" id="r-sm" />
        <label htmlFor="r-sm" className="text-sm text-fg">
          Small
        </label>
      </div>
      <div className="flex items-center gap-2">
        <RadioItem value="medium" id="r-md" />
        <label htmlFor="r-md" className="text-sm text-fg">
          Medium
        </label>
      </div>
      <div className="flex items-center gap-2">
        <RadioItem value="large" id="r-lg" />
        <label htmlFor="r-lg" className="text-sm text-fg">
          Large
        </label>
      </div>
    </RadioGroup>
  ),
  args: {},
};

export const Disabled: Story = {
  render: (args) => (
    <RadioGroup defaultValue="a" aria-label="choice" disabled {...args}>
      <div className="flex items-center gap-2">
        <RadioItem value="a" id="r-a" />
        <label htmlFor="r-a" className="text-sm text-fg">
          Option A
        </label>
      </div>
      <div className="flex items-center gap-2">
        <RadioItem value="b" id="r-b" />
        <label htmlFor="r-b" className="text-sm text-fg">
          Option B
        </label>
      </div>
    </RadioGroup>
  ),
  args: {},
};
