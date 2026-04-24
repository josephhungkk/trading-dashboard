import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ColumnDef } from '@tanstack/react-table';
import { DataTable } from './DataTable';
import { MobileCardRow } from '../MobileCardRow/MobileCardRow';

interface Row {
  id: string;
  symbol: string;
  last: number;
  change: number;
}

const fiveRows: Row[] = [
  { id: '1', symbol: 'AAPL', last: 185.32, change: 2.15 },
  { id: '2', symbol: 'MSFT', last: 392.14, change: -1.03 },
  { id: '3', symbol: 'NVDA', last: 876.21, change: 12.5 },
  { id: '4', symbol: 'TSLA', last: 248.05, change: -3.8 },
  { id: '5', symbol: 'GOOGL', last: 142.78, change: 0.47 },
];

const stress500: Row[] = Array.from({ length: 500 }, (_, i) => ({
  id: String(i + 1).padStart(3, '0'),
  symbol: `SYM${String(i + 1).padStart(3, '0')}`,
  last: 50 + Math.random() * 200,
  change: (Math.random() - 0.5) * 10,
}));

const columns: ColumnDef<Row>[] = [
  { accessorKey: 'symbol', header: 'Symbol' },
  {
    accessorKey: 'last',
    header: 'Last',
    cell: (info) => (info.getValue() as number).toFixed(2),
  },
  {
    accessorKey: 'change',
    header: 'Change',
    cell: (info) => (info.getValue() as number).toFixed(2),
  },
];

const meta = {
  title: 'Patterns/DataTable',
  component: DataTable<Row>,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <div className="h-[30rem]">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof DataTable<Row>>;

export default meta;
type Story = StoryObj<typeof meta>;

export const FiveRows: Story = {
  args: {
    columns,
    data: fiveRows,
    rowKey: (r) => r.id,
  },
};

export const FiveHundredRows: Story = {
  args: {
    columns,
    data: stress500,
    rowKey: (r) => r.id,
  },
};

export const MobileCards: Story = {
  args: {
    columns,
    data: fiveRows,
    rowKey: (r) => r.id,
    mobileRow: (r) => (
      <MobileCardRow
        primary={r.symbol}
        metrics={[
          { label: 'Last', value: r.last.toFixed(2) },
          { label: 'Chg', value: r.change.toFixed(2) },
        ]}
      />
    ),
  },
};

export const Empty: Story = {
  args: {
    columns,
    data: [],
    rowKey: (r) => r.id,
  },
};
