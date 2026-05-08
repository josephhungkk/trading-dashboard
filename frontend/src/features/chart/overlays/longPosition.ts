import type {
  Bounding,
  Coordinate,
  Overlay,
  OverlayFigure,
  OverlayPerformEventParams,
  OverlayTemplate,
} from 'klinecharts';

export type PositionLegType = 'sl' | 'tp';

export interface PositionOverlayExtendData {
  tickSize?: number | null;
  slLegId?: string | null;
  tpLegId?: string | null;
  originalStopLossPrice?: number | null;
  originalTakeProfitPrice?: number | null;
  disabledLegIds?: readonly string[];
}

const ENTRY_COLOR = '#22c55e';
const STOP_COLOR = '#ef4444';
const TARGET_COLOR = '#22c55e';
const PENDING_COLOR = '#facc15';
const HANDLE_WIDTH = 6;
const HANDLE_HEIGHT = 44;

export function snapToTick(price: number, tick: number): number {
  const safeTick = Number.isFinite(tick) && tick > 0 ? tick : 0.01;
  const decimals = decimalPlaces(safeTick);
  return Number((Math.round(price / safeTick) * safeTick).toFixed(decimals));
}

export const longPositionOverlay: OverlayTemplate<PositionOverlayExtendData> = {
  name: 'longPosition',
  totalStep: 3,
  needDefaultPointFigure: false,
  needDefaultXAxisFigure: false,
  needDefaultYAxisFigure: false,
  styles: {
    line: { color: ENTRY_COLOR, size: 1, style: 'solid', dashedValue: [], smooth: false },
    rect: {
      style: 'stroke_fill',
      color: TARGET_COLOR,
      borderColor: TARGET_COLOR,
      borderSize: 1,
      borderStyle: 'solid',
      borderDashedValue: [],
      borderRadius: 4,
    },
  },
  createPointFigures: ({ coordinates, overlay, bounding }) =>
    buildPositionFigures({ coordinates, overlay, bounding }),
  performEventPressedMove(this: Overlay<PositionOverlayExtendData>, params: OverlayPerformEventParams) {
    snapDraggedLeg(this, params);
  },
};

export function buildPositionFigures(params: {
  coordinates: Coordinate[];
  overlay: Overlay<PositionOverlayExtendData>;
  bounding: Bounding;
}): OverlayFigure[] {
  const { coordinates, overlay, bounding } = params;
  const entry = coordinates[0] ?? { x: 0, y: 0 };
  const stopLoss = coordinates[1] ?? entry;
  const takeProfit = coordinates[2] ?? entry;

  return [
    {
      type: 'line',
      attrs: { coordinates: [{ x: 0, y: entry.y }, { x: bounding.width, y: entry.y }] },
      styles: { color: ENTRY_COLOR, size: 1, style: 'solid', dashedValue: [], smooth: false },
      ignoreEvent: true,
    },
    handleFigure('sl', stopLoss.y, bounding, isLegDisabled(overlay, 'sl') ? PENDING_COLOR : STOP_COLOR),
    handleFigure('tp', takeProfit.y, bounding, isLegDisabled(overlay, 'tp') ? PENDING_COLOR : TARGET_COLOR),
  ];
}

function snapDraggedLeg(
  overlay: Overlay<PositionOverlayExtendData>,
  { points, performPointIndex, performPoint }: OverlayPerformEventParams,
): void {
  const legType = legTypeForPointIndex(performPointIndex);
  if (legType === null) return;

  const point = points[performPointIndex];
  if (!point) return;

  if (isLegDisabled(overlay, legType)) {
    const originalValue = legType === 'sl'
      ? overlay.extendData.originalStopLossPrice
      : overlay.extendData.originalTakeProfitPrice;
    if (typeof originalValue === 'number') point.value = originalValue;
    return;
  }

  if (typeof performPoint.value !== 'number' || !Number.isFinite(performPoint.value)) return;
  point.value = snapToTick(performPoint.value, overlay.extendData.tickSize ?? 0.01);
}

function handleFigure(
  key: PositionLegType,
  y: number,
  bounding: Bounding,
  color: string,
): OverlayFigure {
  return {
    key,
    type: 'rect',
    attrs: {
      x: Math.max(0, bounding.width - HANDLE_WIDTH),
      y: y - HANDLE_HEIGHT / 2,
      width: HANDLE_WIDTH,
      height: HANDLE_HEIGHT,
    },
    styles: {
      style: 'stroke_fill',
      color,
      borderColor: color,
      borderSize: 1,
      borderStyle: 'solid',
      borderDashedValue: [],
      borderRadius: 4,
    },
  };
}

function isLegDisabled(overlay: Overlay<PositionOverlayExtendData>, legType: PositionLegType): boolean {
  const legId = legType === 'sl' ? overlay.extendData.slLegId : overlay.extendData.tpLegId;
  return typeof legId === 'string' && overlay.extendData.disabledLegIds?.includes(legId) === true;
}

function legTypeForPointIndex(index: number): PositionLegType | null {
  if (index === 1) return 'sl';
  if (index === 2) return 'tp';
  return null;
}

function decimalPlaces(value: number): number {
  const text = value.toString().toLowerCase();
  const exponent = text.split('e-')[1];
  if (exponent !== undefined) return Number(exponent);
  return text.split('.')[1]?.length ?? 0;
}
