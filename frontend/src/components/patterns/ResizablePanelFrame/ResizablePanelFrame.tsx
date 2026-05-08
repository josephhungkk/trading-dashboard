import * as React from 'react';
import {
  Group,
  Panel,
  Separator,
  type PanelImperativeHandle,
} from 'react-resizable-panels';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Icon } from '@/components/primitives/Icon';
import { cn } from '@/lib/utils';

export interface PanelSpec {
  id: string;
  defaultSize: number;
  minSize?: number;
  collapsible?: boolean;
  collapsedSize?: number;
  content: React.ReactNode;
}

export interface ResizablePanelFrameProps {
  direction: 'horizontal' | 'vertical';
  autoSaveId?: string;
  panels: PanelSpec[];
  className?: string;
}

export function ResizablePanelFrame({
  direction,
  autoSaveId,
  panels,
  className,
}: ResizablePanelFrameProps): React.JSX.Element {
  const refs = React.useRef<Record<string, PanelImperativeHandle | null>>({});

  function toggleCollapse(id: string): void {
    const r = refs.current[id];
    if (!r) return;
    if (r.isCollapsed()) {
      r.expand();
    } else {
      r.collapse();
    }
  }

  return (
    <Group
      orientation={direction}
      id={autoSaveId}
      className={cn('h-full w-full', className)}
    >
      {panels.map((p, i) => (
        <React.Fragment key={p.id}>
          <Panel
            panelRef={(h) => {
              refs.current[p.id] = h;
            }}
            defaultSize={p.defaultSize}
            minSize={p.minSize ?? 10}
            collapsible={p.collapsible ?? false}
            collapsedSize={p.collapsedSize ?? 0}
            id={p.id}
          >
            {p.content}
          </Panel>
          {i < panels.length - 1 && (
            <Separator
              className={cn(
                'group relative flex items-center justify-center bg-border transition-colors duration-150 hover:bg-accent-active',
                direction === 'horizontal' ? 'w-px cursor-col-resize' : 'h-px cursor-row-resize',
              )}
            >
              {p.collapsible && (
                // TODO(phase3-retro): chevron doesn't flip on collapse — needs per-panel isCollapsed state
                // driven by onResize; deferred because Panel has no onCollapse/onExpand prop.
                <button
                  type="button"
                  aria-label={`Toggle ${p.id}`}
                  onClick={() => {
                    toggleCollapse(p.id);
                  }}
                  className="absolute h-6 w-3 rounded-sm bg-panel text-fg-muted opacity-0 transition-opacity duration-150 group-hover:opacity-100"
                >
                  <Icon as={direction === 'horizontal' ? ChevronLeft : ChevronRight} size="sm" />
                </button>
              )}
            </Separator>
          )}
        </React.Fragment>
      ))}
    </Group>
  );
}
