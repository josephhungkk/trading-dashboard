import { describe, expect, it } from 'vitest';
import type { Bounding, Chart, Coordinate, Overlay } from 'klinecharts';
import {
  longPositionOverlay,
  snapToTick,
  type PositionOverlayExtendData,
} from './longPosition';

describe('longPositionOverlay', () => {
  it('snapToTick rounds 184.987 to the nearest 0.01 tick', () => {
    expect(snapToTick(184.987, 0.01)).toBe(184.99);
  });

  it('snapToTick rounds 1234.4 to the nearest 0.5 tick', () => {
    expect(snapToTick(1234.4, 0.5)).toBe(1234.5);
  });

  it('createPointFigures returns entry, SL, and TP figures', () => {
    const figures = longPositionOverlay.createPointFigures?.({
      chart: {} as Chart,
      overlay: overlay(),
      coordinates,
      bounding,
      xAxis: null,
      yAxis: null,
    });

    expect(Array.isArray(figures)).toBe(true);
    expect(figures).toHaveLength(3);
  });

  it('performEventPressedMove updates point[step-1].value to a snapped price', () => {
    const target = overlay();
    const points = [{ value: 184 }, { value: 180 }, { value: 190 }];

    longPositionOverlay.performEventPressedMove?.call(target, {
      currentStep: -1,
      mode: 'normal',
      points,
      performPointIndex: 1,
      performPoint: { value: 184.987 },
    });

    expect(points[1]?.value).toBe(184.99);
  });
});

const coordinates: Coordinate[] = [
  { x: 24, y: 80 },
  { x: 24, y: 120 },
  { x: 24, y: 40 },
];

const bounding: Bounding = {
  width: 320,
  height: 240,
  left: 0,
  right: 320,
  top: 0,
  bottom: 240,
};

function overlay(): Overlay<PositionOverlayExtendData> {
  return {
    extendData: {
      tickSize: 0.01,
      slLegId: 'sl-1',
      tpLegId: 'tp-1',
      originalStopLossPrice: 180,
      originalTakeProfitPrice: 190,
      disabledLegIds: [],
    },
    points: [{ value: 184 }, { value: 180 }, { value: 190 }],
  } as unknown as Overlay<PositionOverlayExtendData>;
}
