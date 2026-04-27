import { describe, expect, it } from 'vitest';
// eslint-disable-next-line boundaries/element-types -- test for pattern helper that consumes service types
import type { Account } from '@/services/types';
// eslint-disable-next-line boundaries/element-types -- test for pattern helper that consumes maintenance state shape
import type { FleetMaintenance } from '@/stores/global/fleet-maintenance';
import { nlvCellState } from './nlv-cell-state';

const baseAccount: Account = {
  id: '11111111-1111-1111-1111-111111111111',
  broker: 'ibkr',
  alias: '',
  mode: 'paper',
  accountNumber: '11111111',
  nlv: 100,
  nlvAt: null,
  baseCurrency: 'USD',
};

const inactiveMaint: FleetMaintenance = {
  active: false,
  window: null,
  until: null,
};

const weekendMaint: FleetMaintenance = {
  active: true,
  window: 'weekend',
  until: new Date('2026-04-26T13:00:00Z'),
};

describe('nlvCellState', () => {
  it('returns placeholder "no data yet" when nlvAt is null', () => {
    const s = nlvCellState({ ...baseAccount, nlvAt: null }, inactiveMaint);
    expect(s).toEqual({ variant: 'placeholder', value: '—', tooltip: 'no data yet' });
  });

  it('returns normal with null tooltip when < 2 min old', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T11:59:00Z'); // 60s old
    const s = nlvCellState({ ...baseAccount, nlvAt }, inactiveMaint, now);
    expect(s.variant).toBe('normal');
    expect(s.value).toBe(100);
    expect(s.tooltip).toBeNull();
  });

  it('returns dim with "N min ago" tooltip when 2-30 min old', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T11:50:00Z'); // 10 min old
    const s = nlvCellState({ ...baseAccount, nlvAt }, inactiveMaint, now);
    expect(s.variant).toBe('dim');
    expect(s.tooltip).toContain('10 min ago');
  });

  it('returns placeholder "stale since" when > 30 min old', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T11:00:00Z'); // 60 min old
    const s = nlvCellState({ ...baseAccount, nlvAt }, inactiveMaint, now);
    expect(s.variant).toBe('placeholder');
    expect(s.value).toBe('—');
    expect(s.tooltip).toContain('stale since');
  });

  it('maintenance-active overrides staleness rule when nlvAt is set', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T10:00:00Z'); // 2h old, would be placeholder
    const s = nlvCellState({ ...baseAccount, nlvAt }, weekendMaint, now);
    expect(s.variant).toBe('normal');
    expect(s.value).toBe(100);
    expect(s.tooltip).toContain('maintenance window ends');
  });

  it('null nlvAt still renders placeholder during maintenance (no synthesized $0.00)', () => {
    const s = nlvCellState({ ...baseAccount, nlvAt: null }, weekendMaint);
    expect(s.variant).toBe('placeholder');
    expect(s.value).toBe('—');
    expect(s.tooltip).toBe('no data yet');
  });
});
