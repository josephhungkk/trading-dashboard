import { describe, it, expect, beforeEach } from 'vitest';
import { useModeStore } from './mode';

describe('useModeStore', () => {
  beforeEach(() => {
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
  });

  it('defaults to paper/idle/no-pending', () => {
    const s = useModeStore.getState();
    expect(s.mode).toBe('paper');
    expect(s.pendingMode).toBeNull();
    expect(s.status).toBe('idle');
  });

  it('requestModeSwitch(live) stages pendingMode without flipping', () => {
    useModeStore.getState().requestModeSwitch('live');
    const s = useModeStore.getState();
    expect(s.mode).toBe('paper');
    expect(s.pendingMode).toBe('live');
  });

  it('confirmModeSwitch flips mode and clears pending', () => {
    useModeStore.getState().requestModeSwitch('live');
    useModeStore.getState().confirmModeSwitch();
    const s = useModeStore.getState();
    expect(s.mode).toBe('live');
    expect(s.pendingMode).toBeNull();
  });

  it('cancelModeSwitch clears pending without flipping', () => {
    useModeStore.getState().requestModeSwitch('live');
    useModeStore.getState().cancelModeSwitch();
    const s = useModeStore.getState();
    expect(s.mode).toBe('paper');
    expect(s.pendingMode).toBeNull();
  });

  it('live→paper is immediate (no pending stage)', () => {
    useModeStore.setState({ mode: 'live' });
    useModeStore.getState().requestModeSwitch('paper');
    const s = useModeStore.getState();
    expect(s.mode).toBe('paper');
    expect(s.pendingMode).toBeNull();
  });
});
