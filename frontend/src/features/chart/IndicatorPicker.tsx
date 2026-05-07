import * as React from 'react';
import { useState } from 'react';
import { useChartStore } from './stores/chartStore';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/primitives/Dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/primitives/Tabs';
import { Checkbox } from '@/components/primitives/Checkbox';
import { Button } from '@/components/primitives/Button';

// 27 klinecharts built-in technical indicators (verified against klinecharts@10.0.0-beta1).
export const TECHNICAL_INDICATORS = [
  'MA', 'EMA', 'SMA', 'BBI', 'BOLL', 'MACD', 'RSI', 'KDJ', 'BIAS', 'ROC',
  'OBV', 'CCI', 'WR', 'DMI', 'SAR', 'VR', 'BRAR', 'MTM', 'EMV', 'PSY',
  'AO', 'AVP', 'CR', 'DMA', 'KC', 'PVT', 'TRIX',
] as const;

export type TechnicalIndicator = (typeof TECHNICAL_INDICATORS)[number];

export interface IndicatorPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Right-drawer style modal for selecting chart indicators.
 * Tabs: Favorites (empty) / Technicals (27 built-ins) / Custom (Chunk F).
 * Multi-select; Apply commits to chartStore.setIndicators.
 *
 * Staged state lives in the inner component so it resets naturally each time
 * the Dialog mounts (open cycle) — no useEffect setState needed.
 */
export function IndicatorPicker({
  open,
  onOpenChange,
}: IndicatorPickerProps): React.JSX.Element {
  const indicators = useChartStore((s) => s.indicators);
  const setIndicators = useChartStore((s) => s.setIndicators);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="
          w-full max-w-md
          max-md:fixed max-md:inset-0 max-md:max-w-full
          max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none
        "
        aria-label="Indicator picker"
      >
        <IndicatorPickerInner
          indicators={indicators}
          setIndicators={setIndicators}
          onOpenChange={onOpenChange}
        />
      </DialogContent>
    </Dialog>
  );
}

interface InnerProps {
  indicators: string[];
  setIndicators: (inds: string[]) => void;
  onOpenChange: (open: boolean) => void;
}

/**
 * Inner content — mounts fresh each open cycle so staged state starts clean
 * from the current store snapshot without needing a useEffect reset.
 */
function IndicatorPickerInner({
  indicators,
  setIndicators,
  onOpenChange,
}: InnerProps): React.JSX.Element {
  // Lazy initialiser: snapshot store value at mount time.
  const [staged, setStaged] = useState<string[]>(() => [...indicators]);

  const handleToggle = (name: string, checked: boolean): void => {
    setStaged((prev) =>
      checked ? [...prev, name] : prev.filter((x) => x !== name),
    );
  };

  const handleApply = (): void => {
    setIndicators(staged);
    onOpenChange(false);
  };

  const handleCancel = (): void => {
    onOpenChange(false);
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>Indicators</DialogTitle>
      </DialogHeader>

      <Tabs defaultValue="technicals" className="flex flex-col">
        <TabsList className="w-full justify-start">
          <TabsTrigger value="favorites">Favorites</TabsTrigger>
          <TabsTrigger value="technicals">Technicals</TabsTrigger>
          <TabsTrigger value="custom">Custom</TabsTrigger>
        </TabsList>

        <TabsContent value="favorites">
          <p className="py-4 text-sm text-fg-muted">No favorites yet.</p>
        </TabsContent>

        <TabsContent value="technicals">
          <div
            className="mt-2 grid max-h-64 grid-cols-3 gap-x-4 gap-y-2 overflow-y-auto pr-1"
            role="group"
            aria-label="Technical indicators"
          >
            {TECHNICAL_INDICATORS.map((name) => (
              <label
                key={name}
                className="flex min-h-[2.75rem] cursor-pointer items-center gap-2 text-sm"
                htmlFor={`indicator-${name}`}
              >
                <Checkbox
                  id={`indicator-${name}`}
                  checked={staged.includes(name)}
                  onCheckedChange={(checked) =>
                    handleToggle(name, checked === true)
                  }
                  aria-label={name}
                />
                <span>{name}</span>
              </label>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="custom">
          <p className="py-4 text-sm text-fg-muted">
            Custom indicators land in Chunk F.
          </p>
        </TabsContent>
      </Tabs>

      <DialogFooter>
        <Button variant="outline" size="sm" type="button" onClick={handleCancel}>
          Cancel
        </Button>
        <Button variant="default" size="sm" type="button" onClick={handleApply}>
          Apply
        </Button>
      </DialogFooter>
    </>
  );
}
