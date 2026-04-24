import type { Meta, StoryObj } from '@storybook/react-vite';
import { ModeToggle } from './ModeToggle';
import { useModeStore } from '@/stores/global/mode';

const meta = {
  title: 'Patterns/ModeToggle',
  component: ModeToggle,
  tags: ['autodocs'],
} satisfies Meta<typeof ModeToggle>;

export default meta;
type Story = StoryObj<typeof meta>;

export const PaperDefault: Story = {
  decorators: [
    (Story) => {
      useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
      return <Story />;
    },
  ],
};

export const LiveActive: Story = {
  decorators: [
    (Story) => {
      useModeStore.setState({ mode: 'live', pendingMode: null, status: 'idle' });
      return <Story />;
    },
  ],
};

export const ConfirmDialogOpen: Story = {
  decorators: [
    (Story) => {
      useModeStore.setState({ mode: 'paper', pendingMode: 'live', status: 'idle' });
      return <Story />;
    },
  ],
};
