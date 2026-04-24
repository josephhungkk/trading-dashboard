import type { Meta, StoryObj } from '@storybook/react-vite';
import * as React from 'react';
import { CollapsibleDrawer } from './CollapsibleDrawer';

const NAV_LINKS = [
  { href: '/overview', label: 'Overview' },
  { href: '/orders', label: 'Orders' },
  { href: '/positions', label: 'Positions' },
  { href: '/watchlist', label: 'Watchlist' },
  { href: '/admin', label: 'Admin' },
  { href: '/settings', label: 'Settings' },
] as const;

function DrawerContent(): React.JSX.Element {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-1 p-4 pt-12">
      {NAV_LINKS.map((link) => (
        <a
          key={link.href}
          href={link.href}
          className="rounded px-3 py-2 text-sm text-fg hover:bg-elevated"
        >
          {link.label}
        </a>
      ))}
    </nav>
  );
}

interface HarnessProps {
  side: 'left' | 'right';
  initialOpen: boolean;
}

function Harness({ side, initialOpen }: HarnessProps): React.JSX.Element {
  const [open, setOpen] = React.useState<boolean>(initialOpen);
  return (
    <div className="relative min-h-screen bg-bg p-6 text-fg">
      <button
        type="button"
        onClick={() => { setOpen(true); }}
        className="rounded border border-border bg-panel px-3 py-2 text-sm text-fg hover:bg-elevated"
      >
        Open drawer
      </button>
      <p className="mt-4 text-sm text-fg-muted">
        Drawer side: <span className="font-medium text-fg">{side}</span>
      </p>
      <CollapsibleDrawer open={open} onOpenChange={setOpen} side={side} title="Primary navigation">
        <DrawerContent />
      </CollapsibleDrawer>
    </div>
  );
}

const meta = {
  title: 'Patterns/CollapsibleDrawer',
  component: CollapsibleDrawer,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
  args: {
    open: false,
    onOpenChange: () => { /* story-local harness manages state */ },
    side: 'left',
    children: null,
  },
} satisfies Meta<typeof CollapsibleDrawer>;

export default meta;
type Story = StoryObj<typeof meta>;

export const LeftOpen: Story = {
  render: () => <Harness side="left" initialOpen={true} />,
};

export const RightOpen: Story = {
  render: () => <Harness side="right" initialOpen={true} />,
};

export const Closed: Story = {
  render: () => <Harness side="left" initialOpen={false} />,
};
