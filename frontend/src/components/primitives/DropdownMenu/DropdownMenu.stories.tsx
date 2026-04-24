import type { Meta, StoryObj } from '@storybook/react-vite';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuGroup,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
} from './DropdownMenu';

const meta = {
  title: 'Primitives/DropdownMenu',
  component: DropdownMenu,
  tags: ['autodocs'],
} satisfies Meta<typeof DropdownMenu>;

export default meta;
type Story = StoryObj<typeof meta>;

const triggerCls =
  'inline-flex h-9 items-center justify-center rounded-md border border-border bg-panel px-3 text-sm text-fg';

export const Basic: Story = {
  render: () => (
    <DropdownMenu>
      <DropdownMenuTrigger className={triggerCls}>Account</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem>Profile</DropdownMenuItem>
        <DropdownMenuItem>Settings</DropdownMenuItem>
        <DropdownMenuItem>Logout</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};

export const WithLabel: Story = {
  render: () => (
    <DropdownMenu>
      <DropdownMenuTrigger className={triggerCls}>Markets</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>Equities</DropdownMenuLabel>
        <DropdownMenuGroup>
          <DropdownMenuItem>Stocks</DropdownMenuItem>
          <DropdownMenuItem>ETFs</DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Derivatives</DropdownMenuLabel>
        <DropdownMenuGroup>
          <DropdownMenuItem>Options</DropdownMenuItem>
          <DropdownMenuItem>Futures</DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};

export const WithSubmenu: Story = {
  render: () => (
    <DropdownMenu>
      <DropdownMenuTrigger className={triggerCls}>Brokers</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem>Interactive Brokers</DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>Futu</DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuItem>HK Account</DropdownMenuItem>
            <DropdownMenuItem>US Account</DropdownMenuItem>
            <DropdownMenuItem>Paper Account</DropdownMenuItem>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuItem>Charles Schwab</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};

export const WithDisabled: Story = {
  render: () => (
    <DropdownMenu>
      <DropdownMenuTrigger className={triggerCls}>Actions</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem>Place Order</DropdownMenuItem>
        <DropdownMenuItem disabled>Cancel Order</DropdownMenuItem>
        <DropdownMenuItem>View Positions</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};
