/**
 * WebSocket client stub. Real connection logic lands in Phase 4
 * when the first broker adapter starts streaming quotes.
 */
export function connectWs(): null {
  if (import.meta.env.DEV) {
    console.info('[ws] stub — real connection lands in Phase 4');
  }
  return null;
}
