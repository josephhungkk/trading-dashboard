import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { within, userEvent } from 'storybook/test';
import { CommandPalette } from './CommandPalette';
import { useCommandsStore } from '@/stores/global/commands';

function OpenOnMount({ children }: { children: React.ReactNode }): React.JSX.Element {
  useEffect(() => {
    useCommandsStore.getState().setOpen(true);
    return () => { useCommandsStore.getState().setOpen(false); };
  }, []);
  return <>{children}</>;
}

function ClosedOnMount({ children }: { children: React.ReactNode }): React.JSX.Element {
  useEffect(() => {
    useCommandsStore.getState().setOpen(false);
  }, []);
  return (
    <div>
      <button
        type="button"
        onClick={() => { useCommandsStore.getState().setOpen(true); }}
        className="rounded border border-border bg-panel px-3 py-2 text-sm text-fg"
      >
        Open (Cmd+K)
      </button>
      {children}
    </div>
  );
}

const meta = {
  title: 'Patterns/CommandPalette',
  component: CommandPalette,
  tags: ['autodocs'],
  parameters: {
    layout: 'fullscreen',
  },
} satisfies Meta<typeof CommandPalette>;

export default meta;
type Story = StoryObj<typeof meta>;

export const DefaultClosed: Story = {
  render: () => (
    <ClosedOnMount>
      <CommandPalette />
    </ClosedOnMount>
  ),
};

export const DefaultOpen: Story = {
  render: () => (
    <OpenOnMount>
      <CommandPalette />
    </OpenOnMount>
  ),
};

export const WithSymbolPrefix: Story = {
  render: () => (
    <OpenOnMount>
      <CommandPalette />
    </OpenOnMount>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement.ownerDocument.body);
    const input = await canvas.findByPlaceholderText(/Type to search/i);
    await userEvent.clear(input);
  },
};

export const WithCommandPrefix: Story = {
  render: () => (
    <OpenOnMount>
      <CommandPalette />
    </OpenOnMount>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement.ownerDocument.body);
    const input = await canvas.findByPlaceholderText(/Type to search/i);
    await userEvent.clear(input);
    await userEvent.type(input, '>');
  },
};

export const WithAccountPrefix: Story = {
  render: () => (
    <OpenOnMount>
      <CommandPalette />
    </OpenOnMount>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement.ownerDocument.body);
    const input = await canvas.findByPlaceholderText(/Type to search/i);
    await userEvent.clear(input);
    await userEvent.type(input, '@');
  },
};

export const WithRoutePrefix: Story = {
  render: () => (
    <OpenOnMount>
      <CommandPalette />
    </OpenOnMount>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement.ownerDocument.body);
    const input = await canvas.findByPlaceholderText(/Type to search/i);
    await userEvent.clear(input);
    await userEvent.type(input, '/');
  },
};

export const WithHelpPrefix: Story = {
  render: () => (
    <OpenOnMount>
      <CommandPalette />
    </OpenOnMount>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement.ownerDocument.body);
    const input = await canvas.findByPlaceholderText(/Type to search/i);
    await userEvent.clear(input);
    await userEvent.type(input, '?');
  },
};
