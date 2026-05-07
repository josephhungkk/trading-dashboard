/**
 * ChartContextMenu — right-click context menu for the chart canvas.
 *
 * Provides:
 *  - Add Indicator — delegates to parent callback (parent opens IndicatorPicker)
 *  - Remove Indicator — submenu of active indicators; removes via chartStore
 *  - Copy Snapshot — delegates to parent callback
 *
 * Position fixed at clientX/clientY of the originating right-click event.
 * Closes on Escape keydown or outside mousedown.
 */
import { useEffect, useRef, useState } from 'react';
import { useChartStore } from './stores/chartStore';

export interface ChartContextMenuProps {
  open: boolean;
  position: { x: number; y: number };
  onClose: () => void;
  onAddIndicator: () => void;
  onCopySnapshot: () => Promise<void>;
}

export function ChartContextMenu({
  open,
  position,
  onClose,
  onAddIndicator,
  onCopySnapshot,
}: ChartContextMenuProps): React.JSX.Element | null {
  const indicators = useChartStore((s) => s.indicators);
  const removeIndicator = useChartStore((s) => s.removeIndicator);
  const ref = useRef<HTMLUListElement>(null);
  const [submenuOpen, setSubmenuOpen] = useState(false);

  // Close the submenu and notify the parent. Called from event handler
  // callbacks (keydown, mousedown) — never directly from an effect body —
  // so react-hooks/set-state-in-effect does not trigger.
  const closeAll = (): void => {
    setSubmenuOpen(false);
    onClose();
  };

  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') closeAll();
    };
    const onOutsideClick = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) closeAll();
    };

    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onOutsideClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onOutsideClick);
    };
    // closeAll is stable within one open session; eslint-disable is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  return (
    <ul
      ref={ref}
      role="menu"
      aria-label="Chart context menu"
      style={{ position: 'fixed', left: position.x, top: position.y, zIndex: 50 }}
      className="min-w-[10rem] rounded border border-border bg-background shadow-md py-1 text-sm"
    >
      {/* Add Indicator */}
      <li role="none">
        <button
          type="button"
          role="menuitem"
          className="w-full text-left px-3 py-2 min-h-[2.75rem] hover:bg-muted transition-colors"
          onClick={() => {
            onAddIndicator();
            closeAll();
          }}
        >
          Add Indicator
        </button>
      </li>

      {/* Remove Indicator (submenu) */}
      {indicators.length > 0 && (
        <li role="none" className="relative">
          <button
            type="button"
            role="menuitem"
            aria-haspopup="menu"
            aria-expanded={submenuOpen}
            className="w-full text-left px-3 py-2 min-h-[2.75rem] hover:bg-muted transition-colors flex items-center justify-between"
            onClick={() => setSubmenuOpen((prev) => !prev)}
          >
            <span>Remove Indicator</span>
            <span aria-hidden="true">›</span>
          </button>
          {submenuOpen && (
            <ul
              role="menu"
              aria-label="Remove indicator submenu"
              className="absolute left-full top-0 min-w-[8rem] rounded border border-border bg-background shadow-md py-1"
            >
              {indicators.map((name) => (
                <li key={name} role="none">
                  <button
                    type="button"
                    role="menuitem"
                    className="w-full text-left px-3 py-2 min-h-[2.75rem] hover:bg-muted transition-colors"
                    onClick={() => {
                      removeIndicator(name);
                      closeAll();
                    }}
                  >
                    {name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </li>
      )}

      {/* Copy Snapshot */}
      <li role="none">
        <button
          type="button"
          role="menuitem"
          className="w-full text-left px-3 py-2 min-h-[2.75rem] hover:bg-muted transition-colors"
          onClick={async () => {
            await onCopySnapshot();
            closeAll();
          }}
        >
          Copy Snapshot
        </button>
      </li>
    </ul>
  );
}
