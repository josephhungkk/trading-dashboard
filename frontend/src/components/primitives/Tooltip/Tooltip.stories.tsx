import type { Meta, StoryObj } from '@storybook/react-vite';
import { Info } from 'lucide-react';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from './Tooltip';
import { Button } from '@/components/primitives/Button/Button';

const meta = {
  title: 'Primitives/Tooltip',
  component: Tooltip,
  tags: ['autodocs'],
} satisfies Meta<typeof Tooltip>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Basic: Story = {
  render: () => (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="outline">Hover me</Button>
        </TooltipTrigger>
        <TooltipContent>Displays helpful context.</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  ),
};

export const OnIcon: Story = {
  render: () => (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label="More info"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-fg-muted hover:bg-muted/10"
          >
            <Info className="h-4 w-4" aria-hidden="true" />
          </button>
        </TooltipTrigger>
        <TooltipContent>Avg cost is denominated in account currency.</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  ),
};

export const Longform: Story = {
  render: () => (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost">Unrealized P&amp;L</Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          The paper profit or loss on open positions, computed as
          (market_price − avg_cost) × quantity in the position&apos;s currency.
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  ),
};
