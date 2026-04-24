import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ColumnDef } from '@tanstack/react-table';
import { expect, within } from 'storybook/test';
import { DataTable } from './DataTable';
import { MobileCardRow } from '../MobileCardRow/MobileCardRow';
import { NumericCell } from '../../primitives/NumericCell/NumericCell';

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

interface WideRow {
  id: string;
  symbol: string;
  metrics: number[];
}

const STRESS_ROW_COUNT = 500;
const STRESS_COL_COUNT = 30;

const stressData: WideRow[] = Array.from({ length: STRESS_ROW_COUNT }, (_, i) => ({
  id: String(i + 1).padStart(4, '0'),
  symbol: `SYM${String(i + 1).padStart(4, '0')}`,
  metrics: Array.from({ length: STRESS_COL_COUNT }, () => (Math.random() - 0.5) * 200),
}));

const stressColumns: ColumnDef<WideRow>[] = [
  { accessorKey: 'symbol', header: 'Symbol' },
  ...Array.from({ length: STRESS_COL_COUNT }, (_, c) => ({
    id: `m${c}`,
    header: `M${c}`,
    cell: ({ row }) => {
      const v = row.original.metrics[c] ?? 0;
      return <NumericCell value={v} digits={2} emphasis={v >= 0 ? 'up' : 'down'} />;
    },
  })) satisfies ColumnDef<WideRow>[],
];

function StressPerfHarness(): React.JSX.Element {
  const slowFramesRef = React.useRef(0);

  React.useEffect(() => {
    if (typeof PerformanceObserver === 'undefined') return undefined;
    const obs = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.duration > 16) {
          slowFramesRef.current += 1;
          console.warn(`[stress-perf] frame budget exceeded: ${entry.duration.toFixed(1)}ms`);
        }
      }
    });
    try {
      obs.observe({ entryTypes: ['measure', 'longtask'] });
    } catch {
      // entryTypes unsupported in test env — silently ignore
    }
    return () => {
      obs.disconnect();
    };
  }, []);

  return (
    <DataTable<WideRow>
      columns={stressColumns}
      data={stressData}
      rowKey={(r) => r.id}
    />
  );
}

export const StressPerf: Story = {
  // args required by the Row-bound Story type even though render ignores them.
  args: {
    columns: [],
    data: [],
    rowKey: () => '',
  },
  render: () => <StressPerfHarness />,
  parameters: {
    a11y: { disable: true },
    docs: {
      description: {
        story: `${STRESS_ROW_COUNT} rows × ${STRESS_COL_COUNT + 1} columns of NumericCell. PerformanceObserver in the harness emits a console warning per frame >16ms; the play function asserts virtualization keeps the rendered DOM bounded so 500 rows do not all materialize at once.`,
      },
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    // The virtualizer must keep the rendered row count well below the data
    // length, otherwise we are not actually virtualizing under load.
    const rows = await canvas.findAllByRole('row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThan(STRESS_ROW_COUNT / 2);
  },
};
