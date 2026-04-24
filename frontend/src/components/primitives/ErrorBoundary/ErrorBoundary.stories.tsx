import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';
import { ErrorBoundary } from './ErrorBoundary';

function BoomButton(): React.JSX.Element {
  const [boom, setBoom] = React.useState(false);
  if (boom) throw new Error('storybook: boom');
  return (
    <button
      type="button"
      onClick={() => setBoom(true)}
      className="rounded-md border border-border bg-panel px-3 py-1.5 text-sm text-fg hover:bg-elevated"
    >
      Trigger error
    </button>
  );
}

const meta = {
  title: 'Primitives/ErrorBoundary',
  component: ErrorBoundary,
  tags: ['autodocs'],
} satisfies Meta<typeof ErrorBoundary>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: { children: null },
  render: () => (
    <ErrorBoundary>
      <BoomButton />
    </ErrorBoundary>
  ),
};

export const CustomFallback: Story = {
  args: { children: null },
  render: () => (
    <ErrorBoundary fallback={<div className="p-4 text-sm text-fg-muted">custom</div>}>
      <BoomButton />
    </ErrorBoundary>
  ),
};

export const FallbackRenderProp: Story = {
  args: { children: null },
  render: () => (
    <ErrorBoundary
      fallback={(error, retry) => (
        <div role="alert" className="p-4">
          <p className="text-sm text-fg">Caught: {error.message}</p>
          <button
            type="button"
            onClick={retry}
            className="mt-2 rounded-md border border-border bg-panel px-3 py-1.5 text-sm text-fg hover:bg-elevated"
          >
            Reset
          </button>
        </div>
      )}
    >
      <BoomButton />
    </ErrorBoundary>
  ),
};
