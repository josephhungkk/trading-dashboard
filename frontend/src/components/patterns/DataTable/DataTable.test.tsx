import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ColumnDef } from '@tanstack/react-table';
import { DataTable } from './DataTable';

class ResizeObserverStub {
  observe(): void {
    /* noop */
  }
  unobserve(): void {
    /* noop */
  }
  disconnect(): void {
    /* noop */
  }
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

// jsdom reports 0 for layout dimensions; the virtualizer needs a non-zero
// scrollElement size to decide any rows are visible. Stub element metrics so
// the scroll container appears 400px tall and items can be rendered.
Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get() {
    return 400;
  },
});
Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  get() {
    return 800;
  },
});
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  get() {
    return 400;
  },
});
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  get() {
    return 800;
  },
});

function mkMql(matches: boolean, q: string): MediaQueryList {
  return {
    matches,
    media: q,
    onchange: null,
    addListener: () => {
      /* noop */
    },
    removeListener: () => {
      /* noop */
    },
    addEventListener: () => {
      /* noop */
    },
    removeEventListener: () => {
      /* noop */
    },
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}

window.matchMedia = (q: string) => mkMql(q.includes('min-width'), q);

interface Row {
  id: string;
  name: string;
  n: number;
}

const data: Row[] = Array.from({ length: 5 }, (_, i) => ({
  id: String(i),
  name: `row-${i}`,
  n: i * 10,
}));

const columns: ColumnDef<Row>[] = [
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'n', header: 'N' },
];

describe('DataTable', () => {
  it('renders column headers in desktop mode', () => {
    window.matchMedia = (q: string) => mkMql(true, q);
    render(
      <div style={{ height: 400 }}>
        <DataTable columns={columns} data={data} rowKey={(r) => r.id} />
      </div>,
    );
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('N')).toBeInTheDocument();
  });

  it('renders cell values', () => {
    window.matchMedia = (q: string) => mkMql(true, q);
    render(
      <div style={{ height: 400 }}>
        <DataTable columns={columns} data={data} rowKey={(r) => r.id} />
      </div>,
    );
    expect(screen.getByText('row-0')).toBeInTheDocument();
    expect(screen.getByText('row-4')).toBeInTheDocument();
  });

  it('renders empty table when data is empty', () => {
    window.matchMedia = (q: string) => mkMql(true, q);
    render(
      <div style={{ height: 400 }}>
        <DataTable columns={columns} data={[]} rowKey={(r) => r.id} />
      </div>,
    );
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.queryByText(/row-/)).not.toBeInTheDocument();
  });

  it('renders mobile cards when viewport is narrow and mobileRow provided', () => {
    window.matchMedia = (q: string) => mkMql(false, q);
    render(
      <div style={{ height: 400 }}>
        <DataTable
          columns={columns}
          data={data}
          rowKey={(r) => r.id}
          mobileRow={(r) => <div data-testid="card">{r.name}</div>}
        />
      </div>,
    );
    expect(screen.getAllByTestId('card').length).toBeGreaterThan(0);
  });
});
