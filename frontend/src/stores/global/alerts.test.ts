import { beforeEach, describe, expect, it } from 'vitest';

import { useAlertsStore } from '@/stores/global/alerts';

function reset(): void {
  useAlertsStore.setState({ recentFires: [], lastSeenAt: null });
}

describe('useAlertsStore', () => {
  beforeEach(() => {
    localStorage.clear();
    reset();
  });

  it('appendFire prepends and caps at 50', () => {
    for (let i = 0; i < 75; i += 1) {
      useAlertsStore.getState().appendFire({
        id: i,
        alert_id: 1,
        fired_at: `2026-05-13T12:${String(i).padStart(2, '0')}:00Z`,
        verdict: 'true',
      });
    }
    const fires = useAlertsStore.getState().recentFires;
    expect(fires).toHaveLength(50);
    expect(fires[0]?.id).toBe(74);
  });

  it('tracks lastSeenAt on append', () => {
    useAlertsStore.getState().appendFire({
      id: 1,
      alert_id: 1,
      fired_at: '2026-05-13T12:00:00Z',
      verdict: 'true',
    });
    expect(useAlertsStore.getState().lastSeenAt).toBe('2026-05-13T12:00:00Z');
  });

  it('appendFire dedupes by id', () => {
    const fire = {
      id: 7,
      alert_id: 1,
      fired_at: '2026-05-13T12:00:00Z',
      verdict: 'true',
    };
    useAlertsStore.getState().appendFire(fire);
    useAlertsStore.getState().appendFire({ ...fire, verdict: 'changed' });
    const fires = useAlertsStore.getState().recentFires;
    expect(fires).toHaveLength(1);
    expect(fires[0]?.verdict).toBe('changed');
  });

  it('mergeFires sorts descending by fired_at and caps', () => {
    useAlertsStore.getState().appendFire({
      id: 1,
      alert_id: 1,
      fired_at: '2026-05-13T12:00:00Z',
      verdict: 'true',
    });
    useAlertsStore.getState().mergeFires([
      { id: 2, alert_id: 1, fired_at: '2026-05-13T13:00:00Z', verdict: 'true' },
      { id: 3, alert_id: 1, fired_at: '2026-05-13T11:00:00Z', verdict: 'true' },
    ]);
    const fires = useAlertsStore.getState().recentFires;
    expect(fires.map((f) => f.id)).toEqual([2, 1, 3]);
    expect(useAlertsStore.getState().lastSeenAt).toBe('2026-05-13T13:00:00Z');
  });

  it('migrate guard rejects non-array recentFires', async () => {
    localStorage.setItem(
      'alerts-global',
      JSON.stringify({
        state: { recentFires: 'not-an-array', lastSeenAt: 5 },
        version: 0,
      }),
    );
    await useAlertsStore.persist.rehydrate();
    expect(useAlertsStore.getState().recentFires).toEqual([]);
    expect(useAlertsStore.getState().lastSeenAt).toBeNull();
  });

  it('migrate guard filters invalid fire entries', async () => {
    localStorage.setItem(
      'alerts-global',
      JSON.stringify({
        state: {
          recentFires: [
            { id: 1, alert_id: 1, fired_at: '2026-05-13T12:00Z', verdict: 'true' },
            { id: 'bad', alert_id: 1, fired_at: 'x', verdict: 'true' },
            null,
          ],
          lastSeenAt: '2026-05-13T12:00Z',
        },
        version: 0,
      }),
    );
    await useAlertsStore.persist.rehydrate();
    expect(useAlertsStore.getState().recentFires).toHaveLength(1);
    expect(useAlertsStore.getState().recentFires[0]?.id).toBe(1);
  });

  it('clear resets to initial state', () => {
    useAlertsStore.getState().appendFire({
      id: 1,
      alert_id: 1,
      fired_at: '2026-05-13T12:00:00Z',
      verdict: 'true',
    });
    useAlertsStore.getState().clear();
    expect(useAlertsStore.getState().recentFires).toEqual([]);
    expect(useAlertsStore.getState().lastSeenAt).toBeNull();
  });
});
