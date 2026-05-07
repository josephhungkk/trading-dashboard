import * as React from 'react';
import { useState, useCallback } from 'react';
import { Square, Pencil, Save, Maximize2, Camera } from 'lucide-react';
import { useChartStore } from './stores/chartStore';
import { IndicatorPicker } from './IndicatorPicker';
import { Button } from '@/components/primitives/Button';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/primitives/Select';

// TODO(v0.9.1): wire save button to instrument_id resolution + etag capture
// from ChartPage and call putChartLayout from services/chartLayouts.ts.

function noop(): void {
  // Fullscreen not supported or rejected — silently ignore.
}

export interface ChartToolbarProps {
  /** Whether the drawings panel is open. Lifted to ChartPage (MED-C). */
  drawingsOpen: boolean;
  /** Callback to toggle the drawings panel. Lifted to ChartPage (MED-C). */
  onToggleDrawings: () => void;
}

/** Top toolbar: chart-type selector, indicators, drawings, save, fullscreen, screenshot. */
export function ChartToolbar({ drawingsOpen, onToggleDrawings }: ChartToolbarProps): React.JSX.Element {
  const chartType = useChartStore((s) => s.chartType);
  const setChartType = useChartStore((s) => s.setChartType);

  const [indicatorOpen, setIndicatorOpen] = useState(false);

  const handleChartTypeChange = useCallback(
    (value: string) => {
      setChartType(value as 'candle' | 'area' | 'bar');
    },
    [setChartType],
  );

  const handleFullscreen = useCallback(() => {
    const container = document.querySelector('[data-chart-container]');
    if (!container) return;
    if (!document.fullscreenElement) {
      container.requestFullscreen().catch(noop);
    } else {
      document.exitFullscreen().catch(noop);
    }
  }, []);

  return (
    <div
      className="flex min-h-[2.75rem] items-center gap-1 border-b border-border px-2 py-1"
      role="toolbar"
      aria-label="Chart controls"
    >
      {/* Chart type selector */}
      <Select value={chartType} onValueChange={handleChartTypeChange}>
        <SelectTrigger
          className="h-[2.75rem] w-auto min-w-[6rem] text-xs md:h-9"
          aria-label="Chart type"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="candle">Candle</SelectItem>
          <SelectItem value="area">Area</SelectItem>
          <SelectItem value="bar">Bar</SelectItem>
        </SelectContent>
      </Select>

      {/* Indicators */}
      <Button
        variant="ghost"
        size="sm"
        type="button"
        className="h-[2.75rem] min-w-[2.75rem] px-2 md:h-9"
        aria-label="Indicators"
        onClick={() => setIndicatorOpen(true)}
      >
        <span className="hidden md:inline">Indicators</span>
        <Square className="h-4 w-4 md:hidden" aria-hidden="true" />
      </Button>

      {/* Drawings — state lifted to ChartPage (MED-C); panel shown by ChartPage */}
      <Button
        variant="ghost"
        size="sm"
        type="button"
        className="h-[2.75rem] min-w-[2.75rem] px-2 md:h-9"
        aria-label="Drawings"
        aria-pressed={drawingsOpen}
        onClick={onToggleDrawings}
      >
        <span className="hidden md:inline">Drawings</span>
        <Pencil className="h-4 w-4 md:hidden" aria-hidden="true" />
      </Button>

      {/* Save layout — TODO(v0.9.1): wire instrument_id + etag */}
      <Button
        variant="ghost"
        size="sm"
        type="button"
        className="h-[2.75rem] min-w-[2.75rem] px-2 md:h-9"
        aria-label="Save layout"
        title="Save layout (instrument_id wiring pending v0.9.1)"
      >
        <span className="hidden md:inline">Save</span>
        <Save className="h-4 w-4 md:hidden" aria-hidden="true" />
      </Button>

      {/* Fullscreen */}
      <Button
        variant="ghost"
        size="sm"
        type="button"
        className="h-[2.75rem] min-w-[2.75rem] px-2 md:h-9"
        aria-label="Toggle fullscreen"
        onClick={handleFullscreen}
      >
        <span className="hidden md:inline">Fullscreen</span>
        <Maximize2 className="h-4 w-4 md:hidden" aria-hidden="true" />
      </Button>

      {/* Screenshot — deferred v0.9.1 */}
      <Button
        variant="ghost"
        size="sm"
        type="button"
        className="h-[2.75rem] min-w-[2.75rem] px-2 opacity-50 md:h-9"
        aria-label="Screenshot (coming soon)"
        title="Coming soon"
        disabled
      >
        <span className="hidden md:inline">Screenshot</span>
        <Camera className="h-4 w-4 md:hidden" aria-hidden="true" />
      </Button>

      <IndicatorPicker open={indicatorOpen} onOpenChange={setIndicatorOpen} />
    </div>
  );
}
