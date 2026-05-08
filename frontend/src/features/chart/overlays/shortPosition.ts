import type { Overlay, OverlayPerformEventParams, OverlayTemplate } from 'klinecharts';
import {
  buildPositionFigures,
  snapToTick,
  type PositionOverlayExtendData,
} from './longPosition';

export { snapToTick };
export type { PositionOverlayExtendData };

export const shortPositionOverlay: OverlayTemplate<PositionOverlayExtendData> = {
  name: 'shortPosition',
  totalStep: 3,
  needDefaultPointFigure: false,
  needDefaultXAxisFigure: false,
  needDefaultYAxisFigure: false,
  styles: {
    line: { color: '#ef4444', size: 1, style: 'solid', dashedValue: [], smooth: false },
    rect: {
      style: 'stroke_fill',
      color: '#22c55e',
      borderColor: '#22c55e',
      borderSize: 1,
      borderStyle: 'solid',
      borderDashedValue: [],
      borderRadius: 4,
    },
  },
  createPointFigures: ({ coordinates, overlay, bounding }) =>
    buildPositionFigures({ coordinates, overlay, bounding }),
  performEventPressedMove(this: Overlay<PositionOverlayExtendData>, params: OverlayPerformEventParams) {
    const { points, performPointIndex, performPoint } = params;
    if (performPointIndex !== 1 && performPointIndex !== 2) return;

    const point = points[performPointIndex];
    if (!point) return;

    const legId = performPointIndex === 1 ? this.extendData.slLegId : this.extendData.tpLegId;
    const isDisabled = typeof legId === 'string' && this.extendData.disabledLegIds?.includes(legId) === true;
    if (isDisabled) {
      const originalValue = performPointIndex === 1
        ? this.extendData.originalStopLossPrice
        : this.extendData.originalTakeProfitPrice;
      if (typeof originalValue === 'number') point.value = originalValue;
      return;
    }

    if (typeof performPoint.value !== 'number' || !Number.isFinite(performPoint.value)) return;
    point.value = snapToTick(performPoint.value, this.extendData.tickSize ?? 0.01);
  },
};
