// Reference:
// - TradingView Pine Script built-ins reference
//   https://www.tradingview.com/pine-script-docs/language/built-ins/
// - klinecharts indicator template typings
//   https://github.com/klinecharts/KLineChart
//
// Notes: Shared deterministic math helpers for custom Phase 9 indicators.

import type { IndicatorTemplate, KLineData } from 'klinecharts';

export type IndicatorValue = number | null;
export type IndicatorOutput = Record<string, IndicatorValue>;

type IndicatorCalc = (dataList: KLineData[], params: number[]) => IndicatorOutput[];

export interface CustomIndicatorSpec {
  name: string;
  shortName: string;
  series: 'normal' | 'price' | 'volume';
  precision: number;
  calcParams: number[];
  shouldOhlc: boolean;
  figures: { key: string; title: string; type: 'line' }[];
  calc: IndicatorCalc;
}

export function createCustomIndicator(spec: CustomIndicatorSpec): IndicatorTemplate<IndicatorOutput, number> {
  return {
    name: spec.name,
    shortName: spec.shortName,
    series: spec.series,
    precision: spec.precision,
    calcParams: spec.calcParams,
    shouldOhlc: spec.shouldOhlc,
    figures: spec.figures,
    calc: (dataList, indicator) => {
      if (dataList.length === 0) return [];
      return spec.calc(dataList, normalizedParams(indicator.calcParams, spec.calcParams));
    },
  };
}

export function emptyRows(dataList: KLineData[], keys: string[]): IndicatorOutput[] {
  return dataList.map(() => Object.fromEntries(keys.map((key) => [key, null])) as IndicatorOutput);
}

export function positiveInt(value: number | undefined, fallback: number): number {
  if (!Number.isFinite(value) || value == null || value < 1) return fallback;
  return Math.max(1, Math.trunc(value));
}

export function numberParam(value: number | undefined, fallback: number): number {
  if (!Number.isFinite(value) || value == null) return fallback;
  return value;
}

export function typicalPrice(bar: KLineData): number {
  return (bar.high + bar.low + bar.close) / 3;
}

export function medianPrice(bar: KLineData): number {
  return (bar.high + bar.low) / 2;
}

export function sourceSeries(dataList: KLineData[], source: 'close' | 'typical' | 'median'): number[] {
  return dataList.map((bar) => {
    if (source === 'typical') return typicalPrice(bar);
    if (source === 'median') return medianPrice(bar);
    return bar.close;
  });
}

export function smaSeries(values: number[], period: number): IndicatorValue[] {
  const out = nullSeries(values.length);
  let sum = 0;
  for (let i = 0; i < values.length; i += 1) {
    sum += valueAt(values, i);
    if (i >= period) sum -= valueAt(values, i - period);
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

export function wmaSeries(values: IndicatorValue[], period: number): IndicatorValue[] {
  const out = nullSeries(values.length);
  const denominator = (period * (period + 1)) / 2;
  for (let i = period - 1; i < values.length; i += 1) {
    let sum = 0;
    let valid = true;
    for (let j = 0; j < period; j += 1) {
      const value = values[i - period + 1 + j];
      if (value == null) {
        valid = false;
        break;
      }
      sum += value * (j + 1);
    }
    if (valid) out[i] = sum / denominator;
  }
  return out;
}

export function emaSeries(values: IndicatorValue[], period: number): IndicatorValue[] {
  const out = nullSeries(values.length);
  const alpha = 2 / (period + 1);
  let sum = 0;
  let count = 0;
  let ema: number | null = null;
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (value == null) {
      out[i] = null;
      continue;
    }
    if (ema == null) {
      sum += value;
      count += 1;
      if (count === period) {
        ema = sum / period;
        out[i] = ema;
      }
      continue;
    }
    ema = value * alpha + ema * (1 - alpha);
    out[i] = ema;
  }
  return out;
}

export function smmaSeries(values: number[], period: number): IndicatorValue[] {
  const out = nullSeries(values.length);
  let sum = 0;
  let smma: number | null = null;
  for (let i = 0; i < values.length; i += 1) {
    const value = valueAt(values, i);
    if (i < period) sum += value;
    if (i === period - 1) {
      smma = sum / period;
      out[i] = smma;
    } else if (i >= period && smma != null) {
      smma = (smma * (period - 1) + value) / period;
      out[i] = smma;
    }
  }
  return out;
}

export function stddev(values: number[], start: number, period: number, mean: number): number {
  let sum = 0;
  for (let i = start; i < start + period; i += 1) {
    const delta = valueAt(values, i) - mean;
    sum += delta * delta;
  }
  return Math.sqrt(sum / period);
}

export function trueRangeSeries(dataList: KLineData[]): number[] {
  return dataList.map((bar, i) => {
    if (i === 0) return bar.high - bar.low;
    const prevClose = dataList[i - 1]?.close ?? bar.close;
    return Math.max(bar.high - bar.low, Math.abs(bar.high - prevClose), Math.abs(bar.low - prevClose));
  });
}

export function atrSeries(dataList: KLineData[], period: number): IndicatorValue[] {
  return rmaSeries(trueRangeSeries(dataList), period);
}

export function rmaSeries(values: number[], period: number): IndicatorValue[] {
  const out = nullSeries(values.length);
  let sum = 0;
  let avg: number | null = null;
  for (let i = 0; i < values.length; i += 1) {
    const value = valueAt(values, i);
    if (i < period) sum += value;
    if (i === period - 1) {
      avg = sum / period;
      out[i] = avg;
    } else if (i >= period && avg != null) {
      avg = (avg * (period - 1) + value) / period;
      out[i] = avg;
    }
  }
  return out;
}

export function highest(dataList: KLineData[], end: number, period: number, field: 'high' | 'low' | 'close'): number | null {
  if (end < period - 1) return null;
  let max = -Infinity;
  for (let i = end - period + 1; i <= end; i += 1) {
    max = Math.max(max, dataList[i]?.[field] ?? max);
  }
  return max;
}

export function lowest(dataList: KLineData[], end: number, period: number, field: 'high' | 'low' | 'close'): number | null {
  if (end < period - 1) return null;
  let min = Infinity;
  for (let i = end - period + 1; i <= end; i += 1) {
    min = Math.min(min, dataList[i]?.[field] ?? min);
  }
  return min;
}

export function highestValue(values: IndicatorValue[], end: number, period: number): number | null {
  if (end < period - 1) return null;
  let max = -Infinity;
  for (let i = end - period + 1; i <= end; i += 1) {
    const value = values[i];
    if (value == null) return null;
    max = Math.max(max, value);
  }
  return max;
}

export function lowestValue(values: IndicatorValue[], end: number, period: number): number | null {
  if (end < period - 1) return null;
  let min = Infinity;
  for (let i = end - period + 1; i <= end; i += 1) {
    const value = values[i];
    if (value == null) return null;
    min = Math.min(min, value);
  }
  return min;
}

export function linreg(values: number[], end: number, period: number, projectionOffset: number): number | null {
  if (end < period - 1) return null;
  const xMean = (period - 1) / 2;
  let ySum = 0;
  for (let i = 0; i < period; i += 1) ySum += valueAt(values, end - period + 1 + i);
  const yMean = ySum / period;
  let numerator = 0;
  let denominator = 0;
  for (let i = 0; i < period; i += 1) {
    const xDelta = i - xMean;
    numerator += xDelta * (valueAt(values, end - period + 1 + i) - yMean);
    denominator += xDelta * xDelta;
  }
  if (denominator === 0) return yMean;
  const slope = numerator / denominator;
  const intercept = yMean - slope * xMean;
  return intercept + slope * (period - 1 + projectionOffset);
}

export const indicatorCalcs = {
  alligator: (dataList, params) => {
    const jawPeriod = positiveInt(params[0], 13);
    const jawShift = positiveInt(params[1], 8);
    const teethPeriod = positiveInt(params[2], 8);
    const teethShift = positiveInt(params[3], 5);
    const lipsPeriod = positiveInt(params[4], 5);
    const lipsShift = positiveInt(params[5], 3);
    const rows = emptyRows(dataList, ['jaw', 'teeth', 'lips']);
    shiftedAssign(rows, 'jaw', smmaSeries(sourceSeries(dataList, 'median') as number[], jawPeriod), jawShift);
    shiftedAssign(rows, 'teeth', smmaSeries(sourceSeries(dataList, 'median') as number[], teethPeriod), teethShift);
    shiftedAssign(rows, 'lips', smmaSeries(sourceSeries(dataList, 'median') as number[], lipsPeriod), lipsShift);
    return rows;
  },
  atr: (dataList, params) => {
    const period = positiveInt(params[0], 14);
    const atr = atrSeries(dataList, period);
    return dataList.map((_, i) => ({ atr: atr[i] ?? null }));
  },
  bbiboll: (dataList, params) => {
    const p1 = positiveInt(params[0], 3);
    const p2 = positiveInt(params[1], 6);
    const p3 = positiveInt(params[2], 12);
    const p4 = positiveInt(params[3], 24);
    const stdevPeriod = positiveInt(params[4], 20);
    const multiplier = numberParam(params[5], 2);
    const close = sourceSeries(dataList, 'close');
    const ma1 = smaSeries(close, p1);
    const ma2 = smaSeries(close, p2);
    const ma3 = smaSeries(close, p3);
    const ma4 = smaSeries(close, p4);
    return dataList.map((_, i) => {
      const values = [ma1[i], ma2[i], ma3[i], ma4[i]];
      if (values.some((value) => value == null) || i < stdevPeriod - 1) return { bbi: null, upper: null, lower: null };
      const bbi = ((values[0] ?? 0) + (values[1] ?? 0) + (values[2] ?? 0) + (values[3] ?? 0)) / 4;
      const dev = stddev(close, i - stdevPeriod + 1, stdevPeriod, bbi);
      return { bbi, upper: bbi + multiplier * dev, lower: bbi - multiplier * dev };
    });
  },
  bbw: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const multiplier = numberParam(params[1], 2);
    const close = sourceSeries(dataList, 'close');
    const middle = smaSeries(close, period);
    return dataList.map((_, i) => {
      const mid = middle[i];
      if (mid == null || mid === 0) return { bbw: null };
      const dev = stddev(close, i - period + 1, period, mid);
      return { bbw: ((mid + multiplier * dev) - (mid - multiplier * dev)) / mid };
    });
  },
  cdp: (dataList) => dataList.map((_, i) => {
    const prev = dataList[i - 1];
    if (prev == null) return { cdp: null, ah: null, nh: null, nl: null, al: null };
    const cdp = (prev.high + prev.low + 2 * prev.close) / 4;
    return {
      cdp,
      ah: cdp + (prev.high - prev.low),
      nh: 2 * cdp - prev.low,
      nl: 2 * cdp - prev.high,
      al: cdp - (prev.high - prev.low),
    };
  }),
  cks: (dataList, params) => {
    const atrPeriod = positiveInt(params[0], 10);
    const stopPeriod = positiveInt(params[1], 9);
    const multiplier = numberParam(params[2], 1);
    const atr = atrSeries(dataList, atrPeriod);
    const longRaw = nullSeries(dataList.length);
    const shortRaw = nullSeries(dataList.length);
    for (let i = 0; i < dataList.length; i += 1) {
      const atrValue = atr[i];
      const high = highest(dataList, i, stopPeriod, 'high');
      const low = lowest(dataList, i, stopPeriod, 'low');
      if (atrValue != null && high != null && low != null) {
        longRaw[i] = high - multiplier * atrValue;
        shortRaw[i] = low + multiplier * atrValue;
      }
    }
    return dataList.map((_, i) => ({
      longStop: highestValue(longRaw, i, stopPeriod),
      shortStop: lowestValue(shortRaw, i, stopPeriod),
    }));
  },
  dc: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    return dataList.map((_, i) => {
      const upper = highest(dataList, i, period, 'high');
      const lower = lowest(dataList, i, period, 'low');
      return { upper, middle: upper == null || lower == null ? null : (upper + lower) / 2, lower };
    });
  },
  dema: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const ema1 = emaSeries(sourceSeries(dataList, 'close'), period);
    const ema2 = emaSeries(ema1, period);
    return dataList.map((_, i) => {
      const first = ema1[i];
      const second = ema2[i];
      return { dema: first == null || second == null ? null : 2 * first - second };
    });
  },
  ene: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const percent = numberParam(params[1], 6) / 100;
    const ma = smaSeries(sourceSeries(dataList, 'close'), period);
    return dataList.map((_, i) => {
      const middle = ma[i] ?? null;
      return { upper: middle == null ? null : middle * (1 + percent), middle, lower: middle == null ? null : middle * (1 - percent) };
    });
  },
  gmma: (dataList) => {
    const close = sourceSeries(dataList, 'close');
    const periods = [3, 5, 8, 10, 12, 15, 30, 35, 40, 45, 50, 60];
    const series = periods.map((period) => emaSeries(close, period));
    return dataList.map((_, i) => Object.fromEntries(series.map((line, index) => [`ema${periods[index]}`, line[i] ?? null])) as IndicatorOutput);
  },
  hma: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const halfPeriod = Math.max(1, Math.floor(period / 2));
    const sqrtPeriod = Math.max(1, Math.floor(Math.sqrt(period)));
    const close = sourceSeries(dataList, 'close');
    const halfWma = wmaSeries(close, halfPeriod);
    const fullWma = wmaSeries(close, period);
    const diff = close.map((_, i) => {
      const half = halfWma[i];
      const full = fullWma[i];
      return half == null || full == null ? null : 2 * half - full;
    });
    const hma = wmaSeries(diff, sqrtPeriod);
    return dataList.map((_, i) => ({ hma: hma[i] ?? null }));
  },
  ichimoku: (dataList, params) => {
    const tenkanPeriod = positiveInt(params[0], 9);
    const kijunPeriod = positiveInt(params[1], 26);
    const spanBPeriod = positiveInt(params[2], 52);
    const displacement = positiveInt(params[3], 26);
    const rows = emptyRows(dataList, ['tenkan', 'kijun', 'senkouA', 'senkouB', 'chikou']);
    for (let i = 0; i < dataList.length; i += 1) {
      const tenkan = midpoint(dataList, i, tenkanPeriod);
      const kijun = midpoint(dataList, i, kijunPeriod);
      rows[i] = { ...rows[i], tenkan, kijun };
      if (tenkan != null && kijun != null && i + displacement < rows.length) rows[i + displacement] = { ...rows[i + displacement], senkouA: (tenkan + kijun) / 2 };
      const senkouB = midpoint(dataList, i, spanBPeriod);
      if (senkouB != null && i + displacement < rows.length) rows[i + displacement] = { ...rows[i + displacement], senkouB };
      if (i - displacement >= 0) rows[i - displacement] = { ...rows[i - displacement], chikou: dataList[i]?.close ?? null };
    }
    return rows;
  },
  kc: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const multiplier = numberParam(params[1], 2);
    const middle = emaSeries(sourceSeries(dataList, 'typical'), period);
    const atr = atrSeries(dataList, period);
    return dataList.map((_, i) => {
      const mid = middle[i] ?? null;
      const range = atr[i];
      return { upper: mid == null || range == null ? null : mid + multiplier * range, middle: mid, lower: mid == null || range == null ? null : mid - multiplier * range };
    });
  },
  lsma: (dataList, params) => {
    const period = positiveInt(params[0], 25);
    const close = sourceSeries(dataList, 'close');
    return dataList.map((_, i) => ({ lsma: linreg(close, i, period, 0) }));
  },
  mike_base: (dataList) => dataList.map((_, i) => {
    const prev = dataList[i - 1];
    if (prev == null) return { s1: null, m: null, r1: null };
    const m = typicalPrice(prev);
    const range = prev.high - prev.low;
    return { s1: m - range, m, r1: m + range };
  }),
  ppsw: (dataList, params) => {
    const period = positiveInt(params[0], 14);
    const multiplier = numberParam(params[1], 1);
    const atr = atrSeries(dataList, period);
    return dataList.map((bar, i) => {
      const middle = typicalPrice(bar);
      const range = atr[i];
      return { upper: range == null ? null : middle + multiplier * range, middle, lower: range == null ? null : middle - multiplier * range, width: range == null ? null : 2 * multiplier * range };
    });
  },
  tema: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const ema1 = emaSeries(sourceSeries(dataList, 'close'), period);
    const ema2 = emaSeries(ema1, period);
    const ema3 = emaSeries(ema2, period);
    return dataList.map((_, i) => {
      const first = ema1[i];
      const second = ema2[i];
      const third = ema3[i];
      return { tema: first == null || second == null || third == null ? null : 3 * first - 3 * second + third };
    });
  },
  tsf: (dataList, params) => {
    const period = positiveInt(params[0], 14);
    const close = sourceSeries(dataList, 'close');
    return dataList.map((_, i) => ({ tsf: linreg(close, i, period, 1) }));
  },
  twap: (dataList) => {
    let sum = 0;
    return dataList.map((bar, i) => {
      sum += typicalPrice(bar);
      return { twap: sum / (i + 1) };
    });
  },
  vwap: (dataList) => {
    let priceVolumeSum = 0;
    let volumeSum = 0;
    return dataList.map((bar) => {
      const volume = bar.volume ?? 0;
      priceVolumeSum += typicalPrice(bar) * volume;
      volumeSum += volume;
      return { vwap: volumeSum === 0 ? null : priceVolumeSum / volumeSum };
    });
  },
  vwma: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const rows = emptyRows(dataList, ['vwma']);
    let priceVolumeSum = 0;
    let volumeSum = 0;
    for (let i = 0; i < dataList.length; i += 1) {
      const bar = dataList[i];
      if (bar == null) continue;
      const volume = bar.volume ?? 0;
      priceVolumeSum += bar.close * volume;
      volumeSum += volume;
      if (i >= period) {
        const old = dataList[i - period];
        const oldVolume = old?.volume ?? 0;
        priceVolumeSum -= (old?.close ?? 0) * oldVolume;
        volumeSum -= oldVolume;
      }
      if (i >= period - 1) rows[i] = { vwma: volumeSum === 0 ? null : priceVolumeSum / volumeSum };
    }
    return rows;
  },
  wma: (dataList, params) => {
    const period = positiveInt(params[0], 20);
    const wma = wmaSeries(sourceSeries(dataList, 'close'), period);
    return dataList.map((_, i) => ({ wma: wma[i] ?? null }));
  },
} satisfies Record<string, IndicatorCalc>;

function normalizedParams(params: number[], defaults: number[]): number[] {
  return defaults.map((fallback, i) => numberParam(params[i], fallback));
}

function nullSeries(length: number): IndicatorValue[] {
  return Array.from({ length }, () => null);
}

function valueAt(values: number[], index: number): number {
  return values[index] ?? 0;
}

function shiftedAssign(rows: IndicatorOutput[], key: string, values: IndicatorValue[], shift: number): void {
  values.forEach((value, i) => {
    const target = i + shift;
    if (value != null && target < rows.length) rows[target] = { ...rows[target], [key]: value };
  });
}

function midpoint(dataList: KLineData[], end: number, period: number): number | null {
  const high = highest(dataList, end, period, 'high');
  const low = lowest(dataList, end, period, 'low');
  return high == null || low == null ? null : (high + low) / 2;
}
