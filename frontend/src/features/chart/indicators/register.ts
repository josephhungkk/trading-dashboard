import { registerIndicator } from 'klinecharts';
import { vwmaIndicator } from './vwma';
import { wmaIndicator } from './wma';
import { temaIndicator } from './tema';
import { demaIndicator } from './dema';
import { hmaIndicator } from './hma';
import { lsmaIndicator } from './lsma';
import { tsfIndicator } from './tsf';
import { gmmaIndicator } from './gmma';
import { alligatorIndicator } from './alligator';
import { twapIndicator } from './twap';
import { ichimokuIndicator } from './ichimoku';
import { vwapIndicator } from './vwap';
import { atrIndicator } from './atr';
import { bbibollIndicator } from './bbiboll';
import { dcIndicator } from './dc';
import { kcIndicator } from './kc';
import { eneIndicator } from './ene';
import { bbwIndicator } from './bbw';
import { cdpIndicator } from './cdp';
import { mikeBaseIndicator } from './mike_base';
import { ppswIndicator } from './ppsw';
import { cksIndicator } from './cks';

const ALL_CUSTOM_INDICATORS = [
  vwmaIndicator,
  wmaIndicator,
  temaIndicator,
  demaIndicator,
  hmaIndicator,
  lsmaIndicator,
  tsfIndicator,
  gmmaIndicator,
  alligatorIndicator,
  twapIndicator,
  ichimokuIndicator,
  vwapIndicator,
  atrIndicator,
  bbibollIndicator,
  dcIndicator,
  kcIndicator,
  eneIndicator,
  bbwIndicator,
  cdpIndicator,
  mikeBaseIndicator,
  ppswIndicator,
  cksIndicator,
];

let registered = false;

export function registerCustomIndicators(): void {
  if (registered) return;
  for (const indicator of ALL_CUSTOM_INDICATORS) {
    registerIndicator(indicator);
  }
  registered = true;
}
