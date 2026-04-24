import type { Meta, StoryObj } from '@storybook/react-vite';
import { Inbox, FolderOpen } from 'lucide-react';
import { EmptyState } from './EmptyState';

const meta = {
  title: 'Patterns/EmptyState',
  component: EmptyState,
  tags: ['autodocs'],
} satisfies Meta<typeof EmptyState>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: { title: 'No items yet' },
};

export const WithIcon: Story = {
  args: {
    icon: Inbox,
    title: 'Your inbox is empty',
    description: 'New messages will appear here.',
  },
};

export const WithAction: Story = {
  args: {
    title: 'No watchlists',
    description: 'Create your first watchlist to track symbols.',
    action: {
      label: 'Create watchlist',
      onClick: () => {
        /* storybook noop */
      },
    },
  },
};

export const FullExample: Story = {
  args: {
    icon: FolderOpen,
    title: 'No saved queries',
    description: 'Save a query from the search bar to see it here.',
    action: {
      label: 'New query',
      onClick: () => {
        /* storybook noop */
      },
    },
  },
};
