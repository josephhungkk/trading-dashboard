import { realAccountsService } from '@/services/accounts';
import type { Account, Mode } from '@/services/types';
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';

export async function fetchAccountsAndSyncMaintenance(mode: Mode): Promise<Account[]> {
  const { accounts, brokerMaintenance } = await realAccountsService.list(mode);
  useFleetMaintenance.getState().setMaintenance({
    active: brokerMaintenance.active,
    window: brokerMaintenance.window,
    until: brokerMaintenance.until ? new Date(brokerMaintenance.until) : null,
  });
  return accounts;
}
