import * as React from 'react';
import { useEffect } from 'react';
import type { Chart, Overlay, OverlayCreate } from 'klinecharts';
import { useActiveStores } from '@/stores/registry';
import type { Position } from '@/services/types';
import { useChartStore } from './stores/chartStore';
import type { PositionOverlayExtendData, PositionLegType } from './overlays';

export interface ModifyRequest {
  legId: string;
  newPrice: number;
  type: PositionLegType;
}

interface PositionOverlayProps {
  canonicalId: string;
  chartRef: React.RefObject<Chart | null>;
  onModifyRequest: (req: ModifyRequest) => void;
}

interface OverlayPosition {
  side: 'BUY' | 'SELL';
  avgPrice: number;
  stopLossPrice: number;
  takeProfitPrice: number;
  slLegId: string;
  tpLegId: string;
}

interface PositionWithFutureFields extends Position {
  canonical_id?: string | null;
  canonicalId?: string | null;
  side?: 'BUY' | 'SELL' | 'buy' | 'sell' | null;
  avgPrice?: number | null;
  bracket?: unknown;
}

interface BracketFields {
  id?: string | null;
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

export function PositionOverlay({
  canonicalId,
  chartRef,
  onModifyRequest,
}: PositionOverlayProps): React.JSX.Element | null {
  const positions = usePositionsForCanonical(canonicalId);
  const tickSize = useInstrumentTickSize(canonicalId) ?? 0.01;
  const pendingModify = useChartStore((s) => s.pending_modify_id);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return undefined;

    const overlayIds: string[] = [];

    for (const position of positions) {
      const overlayPosition = toOverlayPosition(position);
      if (overlayPosition === null) continue;

      const disabledLegIds = [overlayPosition.slLegId, overlayPosition.tpLegId]
        .filter((legId) => pendingModify.has(legId));
      const overlayCreate = buildOverlayCreate({
        position: overlayPosition,
        tickSize,
        disabledLegIds,
        onModifyRequest,
      });
      const overlayId = chart.createOverlay(overlayCreate);
      if (typeof overlayId === 'string') overlayIds.push(overlayId);
    }

    return () => {
      for (const id of overlayIds) {
        chart.removeOverlay({ id });
      }
    };
  }, [positions, tickSize, chartRef, pendingModify, onModifyRequest]);

  return null;
}

export function usePositionsForCanonical(canonicalId: string): PositionWithFutureFields[] {
  const { usePositions } = useActiveStores();
  const positions = usePositions((s) => s.positions);
  return React.useMemo(
    () => positions
      .map((position) => position as PositionWithFutureFields)
      .filter((position) => positionMatchesCanonical(position, canonicalId)),
    [positions, canonicalId],
  );
}

export function useInstrumentTickSize(canonicalId: string): number | null {
  void canonicalId;
  // TODO(Task 44): replace with broker-discovered instruments.tick_size state.
  return null;
}

function buildOverlayCreate(params: {
  position: OverlayPosition;
  tickSize: number;
  disabledLegIds: string[];
  onModifyRequest: (req: ModifyRequest) => void;
}): OverlayCreate {
  const { position, tickSize, disabledLegIds, onModifyRequest } = params;
  const extendData: PositionOverlayExtendData = {
    tickSize,
    slLegId: position.slLegId,
    tpLegId: position.tpLegId,
    originalStopLossPrice: position.stopLossPrice,
    originalTakeProfitPrice: position.takeProfitPrice,
    disabledLegIds,
  };

  return {
    name: position.side === 'BUY' ? 'longPosition' : 'shortPosition',
    lock: false,
    points: [
      { value: position.avgPrice },
      { value: position.stopLossPrice },
      { value: position.takeProfitPrice },
    ],
    extendData,
    onPressedMoveEnd: ({ overlay }) => {
      handlePressedMoveEnd({
        overlay: overlay as Overlay<PositionOverlayExtendData>,
        tickSize,
        position,
        disabledLegIds,
        onModifyRequest,
      });
    },
  };
}

function handlePressedMoveEnd(params: {
  overlay: Overlay<PositionOverlayExtendData>;
  tickSize: number;
  position: OverlayPosition;
  disabledLegIds: string[];
  onModifyRequest: (req: ModifyRequest) => void;
}): void {
  const { overlay, tickSize, position, disabledLegIds, onModifyRequest } = params;
  const stopLossValue = overlay.points[1]?.value;
  const takeProfitValue = overlay.points[2]?.value;

  if (
    typeof stopLossValue === 'number' &&
    !disabledLegIds.includes(position.slLegId) &&
    hasMovedAtLeastOneTick(stopLossValue, position.stopLossPrice, tickSize)
  ) {
    onModifyRequest({ legId: position.slLegId, newPrice: stopLossValue, type: 'sl' });
    return;
  }

  if (
    typeof takeProfitValue === 'number' &&
    !disabledLegIds.includes(position.tpLegId) &&
    hasMovedAtLeastOneTick(takeProfitValue, position.takeProfitPrice, tickSize)
  ) {
    onModifyRequest({ legId: position.tpLegId, newPrice: takeProfitValue, type: 'tp' });
  }
}

function hasMovedAtLeastOneTick(nextPrice: number, currentPrice: number, tickSize: number): boolean {
  return Math.abs(nextPrice - currentPrice) + Number.EPSILON >= tickSize;
}

function positionMatchesCanonical(position: PositionWithFutureFields, canonicalId: string): boolean {
  const positionCanonical = position.canonical_id ?? position.canonicalId ?? position.symbol;
  return positionCanonical === canonicalId;
}

function toOverlayPosition(position: PositionWithFutureFields): OverlayPosition | null {
  const bracket = readBracket(position.bracket);
  if (bracket === null) return null;

  const avgPrice = numberOrNull(position.avgPrice) ?? numberOrNull(position.avgCost);
  const stopLossPrice = numberOrNull(bracket.stopLossPrice) ?? numberOrNull(bracket.stop_loss_price);
  const takeProfitPrice = numberOrNull(bracket.takeProfitPrice) ?? numberOrNull(bracket.take_profit_price);
  const side = readSide(position);

  if (avgPrice === null || stopLossPrice === null || takeProfitPrice === null || side === null) {
    return null;
  }

  const bracketId = readString(bracket.id);
  const slLegId = readString(bracket.stopLossId)
    ?? readString(bracket.stop_loss_id)
    ?? readString(bracket.slLegId)
    ?? (bracketId === null ? null : `${bracketId}:sl`);
  const tpLegId = readString(bracket.takeProfitId)
    ?? readString(bracket.take_profit_id)
    ?? readString(bracket.tpLegId)
    ?? (bracketId === null ? null : `${bracketId}:tp`);

  if (slLegId === null || tpLegId === null) return null;

  return { side, avgPrice, stopLossPrice, takeProfitPrice, slLegId, tpLegId };
}

function readBracket(value: unknown): BracketFields | null {
  if (typeof value !== 'object' || value === null) return null;
  return value as BracketFields;
}

function readSide(position: PositionWithFutureFields): 'BUY' | 'SELL' | null {
  if (position.side === 'BUY' || position.side === 'buy') return 'BUY';
  if (position.side === 'SELL' || position.side === 'sell') return 'SELL';
  if (position.qty > 0) return 'BUY';
  if (position.qty < 0) return 'SELL';
  return null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}
