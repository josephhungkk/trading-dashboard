import { beforeEach, describe, expect, it } from 'vitest';
import { useFleetMaintenance } from './fleet-maintenance';

describe('useFleetMaintenance', () => {
  beforeEach(() => {
    useFleetMaintenance.setState({
      maintenance: { active: false, window: null, until: null },
    });
  });

  it('default is inactive with null window/until', () => {
    expect(useFleetMaintenance.getState().maintenance).toEqual({
      active: false,
      window: null,
      until: null,
    });
  });

  it('setMaintenance with active=true preserves Date until', () => {
    const until = new Date('2026-04-27T00:00:00Z');
    useFleetMaintenance.getState().setMaintenance({
      active: true,
      window: 'weekend',
      until,
    });
    const state = useFleetMaintenance.getState().maintenance;
    expect(state.active).toBe(true);
    expect(state.window).toBe('weekend');
    expect(state.until).toEqual(until);
  });

  it('setMaintenance(active=false) clears window and until back to null', () => {
    useFleetMaintenance.getState().setMaintenance({
      active: true,
      window: 'daily',
      until: new Date('2026-04-26T05:50:00Z'),
    });
    useFleetMaintenance.getState().setMaintenance({
      active: false,
      window: null,
      until: null,
    });
    expect(useFleetMaintenance.getState().maintenance).toEqual({
      active: false,
      window: null,
      until: null,
    });
  });
});
