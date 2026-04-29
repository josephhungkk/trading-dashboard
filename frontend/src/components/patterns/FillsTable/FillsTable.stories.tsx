import type { Meta, StoryObj } from '@storybook/react-vite';
import { FillsTable } from './FillsTable';
import type { FillResponse } from './FillsTable';

function makeFill(
  overrides: Partial<FillResponse> & { id: string; executed_at: string },
): FillResponse {
  return {
    commission: '1.50000000',
    commission_currency: 'USD',
    currency: 'USD',
    exec_id: `exec-${overrides.id}`,
    order_id: `00000000-0000-0000-0000-00000000000${overrides.id}`,
    price: '185.32000000',
    qty: '10.00000000',
    ...overrides,
  };
}

const day1Fills: FillResponse[] = [
  makeFill({ id: '1', executed_at: '2026-04-28T09:31:00Z', price: '185.32000000', qty: '10.00000000' }),
  makeFill({ id: '2', executed_at: '2026-04-28T10:15:00Z', price: '186.10000000', qty: '5.00000000', commission: '0.75000000' }),
  makeFill({ id: '3', executed_at: '2026-04-28T14:22:00Z', price: '184.95000000', qty: '20.00000000', commission: '3.00000000' }),
];

const day2Fills: FillResponse[] = [
  makeFill({ id: '4', executed_at: '2026-04-29T09:05:00Z', price: '188.00000000', qty: '8.00000000', commission: '1.20000000' }),
  makeFill({ id: '5', executed_at: '2026-04-29T11:30:00Z', price: '190.50000000', qty: '3.00000000', commission: '0.45000000' }),
  makeFill({ id: '6', executed_at: '2026-04-29T15:55:00Z', price: '187.25000000', qty: '15.00000000', commission: '2.25000000' }),
];

const mockFills = [...day1Fills, ...day2Fills];

const meta = {
  title: 'Patterns/FillsTable',
  component: FillsTable,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <div className="h-[30rem] overflow-auto border border-border rounded-md">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof FillsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    fills: mockFills,
    hasMore: false,
    isLoading: false,
  },
};

export const WithLoadMore: Story = {
  args: {
    fills: day1Fills,
    hasMore: true,
    isLoading: false,
    onLoadMore: () => {
      console.log('Load more triggered');
    },
  },
};

export const LoadingMore: Story = {
  args: {
    fills: day1Fills,
    hasMore: true,
    isLoading: true,
    onLoadMore: () => undefined,
  },
};

export const EmptyState: Story = {
  args: {
    fills: [],
    hasMore: false,
    isLoading: false,
  },
};

export const Loading: Story = {
  args: {
    fills: [],
    hasMore: false,
    isLoading: true,
  },
};
