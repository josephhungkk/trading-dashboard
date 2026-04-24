import type { Meta, StoryObj } from '@storybook/react-vite';
import { MobileCardRow } from './MobileCardRow';

const meta = {
  title: 'Patterns/MobileCardRow',
  component: MobileCardRow,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <div className="max-w-[24rem] p-3">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof MobileCardRow>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    primary: 'AAPL',
    metrics: [
      { label: 'Last', value: '185.32' },
      { label: 'Chg', value: '+2.15' },
    ],
  },
};

export const WithSecondary: Story = {
  args: {
    primary: 'AAPL',
    secondary: 'Apple Inc.',
    metrics: [
      { label: 'Last', value: '185.32' },
      { label: 'Chg', value: '+2.15' },
      { label: 'Vol', value: '42.1M' },
      { label: 'Mkt Cap', value: '2.88T' },
    ],
  },
};

export const Clickable: Story = {
  args: {
    primary: 'TSLA',
    secondary: 'Tesla, Inc.',
    metrics: [
      { label: 'Last', value: '248.05' },
      { label: 'Chg', value: '-3.80' },
    ],
    onClick: () => {
      /* storybook noop */
    },
  },
};
