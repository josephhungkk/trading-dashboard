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
import { mfiIndicator } from './mfi';
import { aroonIndicator } from './aroon';
import { chopIndicator } from './chop';
import { cmoIndicator } from './cmo';
import { crsiIndicator } from './crsi';
import { stochRsiIndicator } from './stoch_rsi';
import { bopIndicator } from './bop';
import { rviIndicator } from './rvi';
import { rvgiIndicator } from './rvgi';
import { rmiIndicator } from './rmi';
import { erIndicator } from './er';
import { foIndicator } from './fo';
import { fisherIndicator } from './fisher';
import { oscIndicator } from './osc';
import { rcIndicator } from './rc';
import { koIndicator } from './ko';
import { efiIndicator } from './efi';
import { avgvolIndicator } from './avgvol';
import { rvolIndicator } from './rvol';
import { mavolIndicator } from './mavol';
import { wfIndicator } from './wf';
import { nineIndicator } from './nine';
import { hadiffIndicator } from './hadiff';

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
  mfiIndicator,
  aroonIndicator,
  chopIndicator,
  cmoIndicator,
  crsiIndicator,
  stochRsiIndicator,
  bopIndicator,
  rviIndicator,
  rvgiIndicator,
  rmiIndicator,
  erIndicator,
  foIndicator,
  fisherIndicator,
  oscIndicator,
  rcIndicator,
  koIndicator,
  efiIndicator,
  avgvolIndicator,
  rvolIndicator,
  mavolIndicator,
  wfIndicator,
  nineIndicator,
  hadiffIndicator,
];

let registered = false;

export function registerCustomIndicators(): void {
  if (registered) return;
  for (const indicator of ALL_CUSTOM_INDICATORS) {
    registerIndicator(indicator);
  }
  registered = true;
}
