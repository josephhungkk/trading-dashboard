import { registerOverlay as registerKlineOverlay } from 'klinecharts';
import { longPositionOverlay } from './longPosition';
import { shortPositionOverlay } from './shortPosition';

let registered = false;

export function registerCustomOverlays(): void {
  if (registered) return;
  if (typeof registerKlineOverlay !== 'function') return;
  registerKlineOverlay(longPositionOverlay);
  registerKlineOverlay(shortPositionOverlay);
  registered = true;
}

export { longPositionOverlay, snapToTick as snapLongPositionToTick } from './longPosition';
export { shortPositionOverlay, snapToTick as snapShortPositionToTick } from './shortPosition';
export type { PositionOverlayExtendData, PositionLegType } from './longPosition';
