import Decimal from 'decimal.js';

Decimal.set({ rounding: Decimal.ROUND_HALF_EVEN });

const MULT = new Decimal('100');
const Q8 = (d: Decimal): string => d.toFixed(8);

export interface LegInput {
  side: 'buy' | 'sell';
  strike: string;
  expiry: string;
  put_call: 'C' | 'P';
}

export interface ComboEnvelopeResult {
  net_debit_credit: string;
  kind: 'DEBIT' | 'CREDIT';
  max_loss: string | null;
  max_profit: string | null;
  break_even: string[];
}

export function computeEnvelope(
  strategy: string,
  legs: LegInput[],
  mids: Record<number, string>,
): ComboEnvelopeResult {
  const fns: Record<string, (legs: LegInput[], mids: Record<number, string>) => ComboEnvelopeResult> = {
    VERTICAL: _vertical,
    CALENDAR: _calendar,
    DIAGONAL: _calendar,
    STRADDLE: _straddle,
    STRANGLE: _strangle,
  };
  const fn = fns[strategy];
  if (!fn) throw new Error(`Unknown strategy: ${strategy}`);
  return fn(legs, mids);
}

function _signedPremium(leg: LegInput, mid: Decimal): Decimal {
  return leg.side === 'sell' ? mid : mid.neg();
}

function _vertical(legs: LegInput[], mids: Record<number, string>): ComboEnvelopeResult {
  const net = legs.reduce((acc, l, i) => acc.plus(_signedPremium(l, new Decimal(mids[i] ?? '0'))), new Decimal(0));
  const buyLeg = legs.find((l) => l.side === 'buy');
  const sellLeg = legs.find((l) => l.side === 'sell');
  if (!buyLeg || !sellLeg) throw new Error('VERTICAL requires one buy and one sell leg');
  const nd = net.abs();
  const spread = new Decimal(buyLeg.strike).minus(sellLeg.strike).abs();
  if (net.lt(0)) {
    return {
      net_debit_credit: Q8(nd),
      kind: 'DEBIT',
      max_loss: Q8(nd.times(MULT)),
      max_profit: Q8(spread.minus(nd).times(MULT)),
      break_even: [Q8(new Decimal(buyLeg.strike).plus(nd))],
    };
  }
  return {
    net_debit_credit: Q8(nd),
    kind: 'CREDIT',
    max_profit: Q8(nd.times(MULT)),
    max_loss: Q8(spread.minus(nd).times(MULT)),
    break_even: [Q8(new Decimal(sellLeg.strike).plus(nd))],
  };
}

function _calendar(legs: LegInput[], mids: Record<number, string>): ComboEnvelopeResult {
  const net = legs.reduce((acc, l, i) => acc.plus(_signedPremium(l, new Decimal(mids[i] ?? '0'))), new Decimal(0));
  const nd = net.abs();
  const kind = net.lt(0) ? 'DEBIT' : 'CREDIT';
  return {
    net_debit_credit: Q8(nd),
    kind,
    max_loss: Q8(nd.times(MULT)),
    max_profit: null,
    break_even: [],
  };
}

function _straddle(legs: LegInput[], mids: Record<number, string>): ComboEnvelopeResult {
  const net = legs.reduce((acc, l, i) => acc.plus(_signedPremium(l, new Decimal(mids[i] ?? '0'))), new Decimal(0));
  const nd = net.abs();
  const strike = new Decimal(legs[0]?.strike ?? '0');
  if (net.lt(0)) {
    return {
      net_debit_credit: Q8(nd),
      kind: 'DEBIT',
      max_loss: Q8(nd.times(MULT)),
      max_profit: null,
      break_even: [Q8(strike.minus(nd)), Q8(strike.plus(nd))],
    };
  }
  return {
    net_debit_credit: Q8(nd),
    kind: 'CREDIT',
    max_profit: Q8(nd.times(MULT)),
    max_loss: null,
    break_even: [],
  };
}

function _strangle(legs: LegInput[], mids: Record<number, string>): ComboEnvelopeResult {
  const net = legs.reduce((acc, l, i) => acc.plus(_signedPremium(l, new Decimal(mids[i] ?? '0'))), new Decimal(0));
  const nd = net.abs();
  const putLeg = legs.find((l) => l.put_call === 'P');
  const callLeg = legs.find((l) => l.put_call === 'C');
  if (!putLeg || !callLeg) throw new Error('STRANGLE requires one put and one call leg');
  if (net.lt(0)) {
    return {
      net_debit_credit: Q8(nd),
      kind: 'DEBIT',
      max_loss: Q8(nd.times(MULT)),
      max_profit: null,
      break_even: [Q8(new Decimal(putLeg.strike).minus(nd)), Q8(new Decimal(callLeg.strike).plus(nd))],
    };
  }
  return {
    net_debit_credit: Q8(nd),
    kind: 'CREDIT',
    max_profit: Q8(nd.times(MULT)),
    max_loss: null,
    break_even: [],
  };
}
