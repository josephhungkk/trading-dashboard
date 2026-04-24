import type { Meta, StoryObj } from '@storybook/react-vite';
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from './Popover';
import { Button } from '@/components/primitives/Button/Button';
import { Input } from '@/components/primitives/Input/Input';

const meta = {
  title: 'Primitives/Popover',
  component: Popover,
  tags: ['autodocs'],
} satisfies Meta<typeof Popover>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Basic: Story = {
  render: () => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Open popover</Button>
      </PopoverTrigger>
      <PopoverContent>
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-semibold text-fg">Notifications</h4>
          <p className="text-xs text-fg-muted">
            You have 3 unread alerts across your accounts.
          </p>
        </div>
      </PopoverContent>
    </Popover>
  ),
};

export const WithForm: Story = {
  render: () => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Set threshold</Button>
      </PopoverTrigger>
      <PopoverContent>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1 text-xs text-fg-muted">
            <label htmlFor="popover-symbol">Symbol</label>
            <Input id="popover-symbol" placeholder="AAPL" />
          </div>
          <div className="flex flex-col gap-1 text-xs text-fg-muted">
            <label htmlFor="popover-price">Price</label>
            <Input id="popover-price" variant="numeric" placeholder="150.00" />
          </div>
          <Button size="sm">Save</Button>
        </div>
      </PopoverContent>
    </Popover>
  ),
};

export const AlignedStart: Story = {
  render: () => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Align start</Button>
      </PopoverTrigger>
      <PopoverContent align="start">
        <p className="text-sm text-fg">Anchored to the start of the trigger.</p>
      </PopoverContent>
    </Popover>
  ),
};
