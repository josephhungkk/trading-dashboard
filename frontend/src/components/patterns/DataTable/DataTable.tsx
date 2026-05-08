import * as React from 'react';
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
  type Row,
} from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
// eslint-disable-next-line boundaries/element-types -- usemediaquery is a shared layout hook
import { useMediaQuery } from '@/hooks/use-media-query';
import { cn } from '@/lib/utils';

export interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  data: T[];
  rowKey: (row: T) => string;
  mobileRow?: (row: T) => React.ReactNode;
  rowHeight?: number;
  className?: string;
}

export function DataTable<T>({
  columns,
  data,
  rowKey,
  mobileRow,
  rowHeight = 36,
  className,
}: DataTableProps<T>): React.JSX.Element {
  const isDesktop = useMediaQuery('(min-width: 48rem)');
  const parentRef = React.useRef<HTMLDivElement>(null);
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: rowKey,
  });
  const rows = table.getRowModel().rows;

  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 6,
  });

  if (!isDesktop && mobileRow) {
    return (
      <div ref={parentRef} className={cn('h-full overflow-auto', className)}>
        <div
          style={{
            height: `${virtual.getTotalSize()}px`,
            position: 'relative',
          }}
        >
          {virtual.getVirtualItems().map((v) => {
            const row: Row<T> | undefined = rows[v.index];
            if (!row) return null;
            return (
              <div
                key={v.key}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${v.start}px)`,
                }}
              >
                {mobileRow(row.original)}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div ref={parentRef} className={cn('h-full overflow-auto', className)}>
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-panel">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th key={h.id} className="px-3 py-2 text-left text-fg-muted">
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        {/* TODO(phase3-retro): virtualizer uses absolute tr inside tbody — refactor to div-grid */}
        <tbody
          style={{
            height: `${virtual.getTotalSize()}px`,
            position: 'relative',
          }}
        >
          {virtual.getVirtualItems().map((v) => {
            const row: Row<T> | undefined = rows[v.index];
            if (!row) return null;
            return (
              <tr
                key={row.id}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${v.start}px)`,
                  height: `${rowHeight}px`,
                }}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
