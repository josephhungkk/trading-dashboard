// eslint-disable-next-line boundaries/element-types -- AccountPicker helper evaluates account service data shape
import type { Account } from '@/services/types';
// eslint-disable-next-line boundaries/element-types -- AccountPicker helper evaluates global maintenance state shape
import type { FleetMaintenance } from '@/stores/global/fleet-maintenance';

export type NlvCellState =
  | { variant: 'normal'; value: number; tooltip: string | null }
  | { variant: 'dim'; value: number; tooltip: string }
  | { variant: 'placeholder'; value: string; tooltip: string };

const formatTime = (d: Date): string =>
  d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

export function nlvCellState(
  account: Account,
  maintenance: FleetMaintenance,
  now = new Date(),
): NlvCellState {
  // Null NLV always renders as placeholder, even during maintenance —
  // suppressing the staleness rule does not synthesize numeric data.
  if (account.nlvAt === null) {
    return { variant: 'placeholder', value: '—', tooltip: 'no data yet' };
  }

  if (maintenance.active && maintenance.until != null) {
    return {
      variant: 'normal',
      value: account.nlv,
      tooltip: `maintenance window ends at ${formatTime(maintenance.until)}`,
    };
  }

  const ageSec = (now.getTime() - account.nlvAt.getTime()) / 1000;

  if (ageSec < 120) {
    return { variant: 'normal', value: account.nlv, tooltip: null };
  }

  if (ageSec < 1800) {
    return {
      variant: 'dim',
      value: account.nlv,
      tooltip: `as of ${formatTime(account.nlvAt)} (${Math.round(ageSec / 60)} min ago)`,
    };
  }

  return {
    variant: 'placeholder',
    value: '—',
    tooltip: `stale since ${formatTime(account.nlvAt)}`,
  };
}
