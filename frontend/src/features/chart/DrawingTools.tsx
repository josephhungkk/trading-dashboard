/**
 * DrawingTools — left-rail drawing-tool selector for klinecharts built-in overlays.
 *
 * Verified tool list sourced from klinecharts/dist/index.esm.js (v10-beta1).
 * The spec (§8) listed additional tools (fibonacciSegment, fibonacciCircle,
 * fibonacciSpiral, fibonacciSpeedResistanceFan, fibonacciExtension, gannBox,
 * andrewsPitchfork, parallelogram, triangle) that are NOT present as built-in
 * overlays in the bundled ESM — only fibonacciLine was found. The custom overlays
 * (Long/Short Position, Pitchfork variants) are deferred to Chunk G.
 *
 * TODO(Task wiring): TradeChart needs to call `chart.createOverlay({ name })`
 * when `activeDrawingTool` changes in chartStore. This component only manages
 * the selection state; the actual klinecharts integration is a separate wiring step.
 */
import * as React from 'react';
import { useChartStore } from './stores/chartStore';

/**
 * Built-in klinecharts overlay names verified against
 * node_modules/klinecharts/dist/index.esm.js (v10-beta1, 2026-05-08).
 * Spec-listed names absent from the bundle are noted above.
 */
export const DRAWING_TOOLS = [
  'horizontalStraightLine',
  'verticalStraightLine',
  'straightLine',
  'horizontalRayLine',
  'verticalRayLine',
  'rayLine',
  'horizontalSegment',
  'verticalSegment',
  'segment',
  'priceLine',
  'priceChannelLine',
  'parallelStraightLine',
  'fibonacciLine',
  'rect',
  'circle',
  'arc',
  'price',
  'simpleAnnotation',
  'simpleTag',
] as const;

export type DrawingToolName = (typeof DRAWING_TOOLS)[number];

/** Derive a compact 3-letter label for display in the icon button. */
function toolLabel(name: string): string {
  return name.replace(/([A-Z])/g, ' $1').trim().slice(0, 3).toUpperCase();
}

/**
 * Left-rail drawing tool selector.
 * Desktop: vertical strip pinned to the chart left edge.
 * Mobile: collapses (hidden by default, toggled by parent via CSS class).
 */
export function DrawingTools(): React.JSX.Element {
  const activeTool = useChartStore((s) => s.activeDrawingTool);
  const setActiveTool = useChartStore((s) => s.setActiveDrawingTool);

  return (
    <div
      className="flex flex-col gap-1 p-1 bg-background border-r border-border overflow-y-auto"
      role="toolbar"
      aria-label="Drawing tools"
      aria-orientation="vertical"
    >
      {DRAWING_TOOLS.map((name) => {
        const isActive = name === activeTool;
        return (
          <button
            type="button"
            key={name}
            aria-label={name}
            aria-pressed={isActive}
            title={name}
            onClick={() => setActiveTool(isActive ? null : name)}
            className={[
              'flex items-center justify-center',
              'min-h-[2.75rem] min-w-[2.75rem]',
              'rounded text-xs font-mono leading-none',
              'transition-colors',
              isActive
                ? 'bg-primary text-primary-foreground'
                : 'hover:bg-muted text-foreground',
            ].join(' ')}
          >
            {toolLabel(name)}
          </button>
        );
      })}
    </div>
  );
}
