import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';
import { Toaster } from './Toast';
import { useToast } from '@/hooks/use-toast';
import { Button } from '@/components/primitives/Button/Button';

const meta = {
  title: 'Primitives/Toast',
  component: Toaster,
  tags: ['autodocs'],
} satisfies Meta<typeof Toaster>;

export default meta;
type Story = StoryObj<typeof meta>;

function BasicDemo(): React.JSX.Element {
  const { toast } = useToast();
  return (
    <div className="flex flex-col gap-4">
      <Button
        onClick={() =>
          toast({
            title: 'Order received',
            description: 'Routing to IBKR for execution.',
          })
        }
      >
        Push neutral toast
      </Button>
      <Toaster />
    </div>
  );
}

function SuccessDemo(): React.JSX.Element {
  const { toast } = useToast();
  return (
    <div className="flex flex-col gap-4">
      <Button
        onClick={() =>
          toast({
            tone: 'success',
            title: 'Order filled',
            description: '100 shares AAPL at 175.23',
          })
        }
      >
        Push success toast
      </Button>
      <Toaster />
    </div>
  );
}

function ErrorDemo(): React.JSX.Element {
  const { toast } = useToast();
  return (
    <div className="flex flex-col gap-4">
      <Button
        variant="destructive"
        onClick={() =>
          toast({
            tone: 'error',
            title: 'Order rejected',
            description: 'Insufficient buying power.',
          })
        }
      >
        Push error toast
      </Button>
      <Toaster />
    </div>
  );
}

function WithActionDemo(): React.JSX.Element {
  const { toast } = useToast();
  return (
    <div className="flex flex-col gap-4">
      <Button
        onClick={() =>
          toast({
            title: 'Connection lost',
            description: 'IBKR gateway is offline.',
            durationMs: 0,
          })
        }
      >
        Push sticky toast (no auto-dismiss)
      </Button>
      <Toaster />
    </div>
  );
}

export const Basic: Story = { render: () => <BasicDemo /> };
export const Success: Story = { render: () => <SuccessDemo /> };
export const Error: Story = { render: () => <ErrorDemo /> };
export const WithAction: Story = { render: () => <WithActionDemo /> };
