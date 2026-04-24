import type { Mode } from '@/services/types';
import { createScopedStores, type ScopedStores } from './factory';
import { useModeStore } from './global/mode';

const live  = createScopedStores('live');
const paper = createScopedStores('paper');

export function getScopedStores<M extends Mode>(mode: M): ScopedStores<M> {
  return (mode === 'live' ? live : paper) as unknown as ScopedStores<M>;
}
export function useActiveStores(): ScopedStores<Mode> {
  const mode = useModeStore(s => s.mode);
  return getScopedStores(mode);
}
export function getBothScopes() { return { live, paper }; }
