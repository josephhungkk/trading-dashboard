/**
 * Map an exchange code to the correct Noto CJK variant lang tag.
 * Phase 3 populates the real mapping; Phase 0 returns 'en' for everything
 * since no stock names render yet.
 */
export function langForMarket(_exchange: string): string {
  return 'en';
}
