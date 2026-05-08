import * as React from 'react';
import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useToast } from '@/hooks/use-toast';
import { TradeChart } from './TradeChart';
import { ChartToolbar } from './ChartToolbar';
import { ChartLayoutSync } from './ChartLayoutSync';
import { TimeframeBar } from './TimeframeBar';
import { DrawingTools } from './DrawingTools';
import { ConfirmDialog } from './ConfirmDialog';
import type { ModifyRequest } from './PositionOverlay';
import { useInstrumentTickSize, usePositionsForCanonical } from './PositionOverlay';
import { useChartStore } from './stores/chartStore';
// HIGH-1: subscribeOrderEvents NOT imported here — /ws/orders backend endpoint does
// not exist yet; opening it leaks the JWT via Sec-WebSocket-Protocol on every confirm.
// TODO(Phase 10): re-enable when /ws/orders is wired on the backend.
import { getOrderState } from './services/orders';
// getChartLayout import deferred until instrument_id resolution lands (Task 37).

interface ChartPageProps {
  canonicalId: string;
}

interface ModifyDialogRequest extends ModifyRequest {
  currentPrice: number;
}

export function ChartPage({ canonicalId }: ChartPageProps): React.JSX.Element {
  // MED-C: drawingsOpen lifted from ChartToolbar so ChartPage can show DrawingTools panel.
  // TODO(Chunk G): replace placeholder with full DrawingTools panel integration.
  const [drawingsOpen, setDrawingsOpen] = useState(false);
  const [modifyReq, setModifyReq] = useState<ModifyDialogRequest | null>(null);
  const positions = usePositionsForCanonical(canonicalId);
  const tickSize = useInstrumentTickSize(canonicalId) ?? 0.01;
  const setPendingModify = useChartStore((s) => s.setPendingModify);
  const { toast } = useToast();
  const instrumentId: number | null = null; // TODO(Task 37): resolve from canonicalId.

  // HIGH-2: track all in-flight settle callbacks so unmount can drain them.
  const inflightSettlersRef = useRef<Set<() => void>>(new Set());

  // HIGH-2: on unmount, settle any in-flight modifies to avoid state leaks.
  // Capture ref value into a local so the cleanup closure has a stable reference.
  React.useEffect(() => {
    const settlers = inflightSettlersRef.current;
    return () => {
      for (const settle of settlers) settle();
      settlers.clear();
    };
  }, []);

  // TODO(Task 37): resolve instrument_id from canonicalId via API.
  // For now pass 0 as a placeholder; getChartLayout returns null for unknown ids.
  const { isLoading, error } = useQuery({
    queryKey: ['chart-layouts', canonicalId],
    // MED-E: disabled until instrument_id resolution lands; avoids spurious 404s.
    // TODO(Task 37): enable when instrument_id resolution is wired.
    queryFn: async () => null,
    enabled: false,
  });

  const handleModifyRequest = React.useCallback((req: ModifyRequest) => {
    const currentPrice = readCurrentLegPrice(positions, req.legId, req.type);
    if (currentPrice === null) {
      toast({ title: 'Modify failed', description: 'Could not read current leg price.', tone: 'error' });
      return;
    }
    setModifyReq({ ...req, currentPrice });
  }, [positions, toast]);

  // HIGH-2: settle pattern — idempotent, tracked in ref for unmount cleanup.
  // HIGH-1: subscribeOrderEvents removed; 5s getOrderState is the only reconciliation path.
  // MED-2: nonce held in closure only, not stored in shared store.
  const handleConfirmed = React.useCallback((nonce: string) => {
    if (modifyReq === null) return;
    const confirmedReq = modifyReq;

    // MED-2: nonce is held in closure; store only carries targetPrice + startedAt.
    setPendingModify(confirmedReq.legId, {
      targetPrice: confirmedReq.newPrice,
      startedAt: Date.now(),
    });
    setModifyReq(null);

    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const settle = (): void => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      setPendingModify(confirmedReq.legId, null);
      inflightSettlersRef.current.delete(settle);
    };

    inflightSettlersRef.current.add(settle);

    // 5s fallthrough — only operational reconciliation path until Phase 10 /ws/orders.
    timer = setTimeout(() => {
      void getOrderState(confirmedReq.legId).finally(settle);
    }, 5000);

    // nonce is captured in closure for future /ws/orders correlation (Phase 10).
    void nonce;
  }, [modifyReq, setPendingModify]);

  const handleError = React.useCallback((reason: string) => {
    toast({ title: 'Modify failed', description: reason, tone: 'error' });
    setModifyReq(null);
  }, [toast]);

  return (
    <div className="flex h-full flex-col" data-chart-container>
      <ChartLayoutSync
        instrumentId={instrumentId}
        onConflict={() => {
          toast({
            title: 'Layout updated elsewhere',
            description: 'Your chart layout was changed in another tab/session.',
            tone: 'neutral',
          });
        }}
        onError={(reason) => {
          toast({ title: 'Layout sync failed', description: reason, tone: 'error' });
        }}
      />
      <ChartToolbar
        drawingsOpen={drawingsOpen}
        onToggleDrawings={() => setDrawingsOpen((prev) => !prev)}
      />
      <h1 className="px-2 pt-1 text-lg font-semibold">Chart — {canonicalId}</h1>
      <div className="relative flex min-h-0 flex-1">
        {/* Drawings panel — Chunk G integration pending */}
        {drawingsOpen && (
          <div data-testid="drawings-panel" className="w-12 shrink-0">
            <DrawingTools />
          </div>
        )}
        <div className="relative min-h-0 flex-1 rounded border border-border">
          {isLoading && <p>Loading…</p>}
          {error && <p role="alert">Failed to load chart</p>}
          {!isLoading && !error && (
            <TradeChart canonicalId={canonicalId} onModifyRequest={handleModifyRequest} />
          )}
        </div>
      </div>
      <ConfirmDialog
        open={modifyReq !== null}
        legId={modifyReq?.legId ?? ''}
        type={modifyReq?.type ?? 'sl'}
        currentPrice={modifyReq?.currentPrice ?? 0}
        newPrice={modifyReq?.newPrice ?? 0}
        tickSize={tickSize}
        onCancel={() => setModifyReq(null)}
        onConfirmed={handleConfirmed}
        onError={handleError}
      />
      <TimeframeBar />
    </div>
  );
}

function readCurrentLegPrice(
  positions: ReturnType<typeof usePositionsForCanonical>,
  legId: string,
  type: 'sl' | 'tp',
): number | null {
  for (const position of positions) {
    const bracket = readBracket(position.bracket);
    if (bracket === null) continue;

    const id = type === 'sl'
      ? readString(bracket.stopLossId) ?? readString(bracket.stop_loss_id) ?? readString(bracket.slLegId)
      : readString(bracket.takeProfitId) ?? readString(bracket.take_profit_id) ?? readString(bracket.tpLegId);
    if (id !== legId) continue;

    return type === 'sl'
      ? numberOrNull(bracket.stopLossPrice) ?? numberOrNull(bracket.stop_loss_price)
      : numberOrNull(bracket.takeProfitPrice) ?? numberOrNull(bracket.take_profit_price);
  }
  return null;
}

interface ChartPageBracketFields {
  stopLossId?: string | null;
  stop_loss_id?: string | null;
  slLegId?: string | null;
  takeProfitId?: string | null;
  take_profit_id?: string | null;
  tpLegId?: string | null;
  stopLossPrice?: number | null;
  stop_loss_price?: number | null;
  takeProfitPrice?: number | null;
  take_profit_price?: number | null;
}

function readBracket(value: unknown): ChartPageBracketFields | null {
  if (typeof value !== 'object' || value === null) return null;
  return value as ChartPageBracketFields;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}
