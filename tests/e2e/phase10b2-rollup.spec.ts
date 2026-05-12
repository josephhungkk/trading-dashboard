import { expect, test } from '@playwright/test';

/**
 * Phase 10b.2 E1 — multi-account portfolio rollup E2E smoke.
 *
 * Three lightweight smokes (no FX or balance-snapshot fixtures required):
 *   1. /portfolio/rollup page mounts with KPI bar + 2 lower panels visible
 *   2. window toggle (intraday → 30d) updates the URL search param
 *   3. clicking an exposure row opens the drill drawer
 *
 * Deep assertions on the rollup math live in backend goldens
 * (test_portfolio_rollup_service.py). Per-component behavior lives in
 * Vitest specs. Playwright here only verifies the wiring holds.
 */

test.describe('Phase 10b.2 — portfolio rollup', () => {
  test('@smoke /portfolio/rollup page mounts with KPI + panels', async ({
    page,
  }) => {
    await page.goto('/portfolio/rollup');
    // Wait for the loading state to clear OR for the page shell to be visible.
    await expect(page.getByTestId('rollup-page')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('rollup-kpi-bar')).toBeVisible();
    await expect(page.getByTestId('rollup-curve-chart')).toBeVisible();
    await expect(page.getByTestId('rollup-per-account-table')).toBeVisible();
    await expect(page.getByTestId('rollup-exposure-list')).toBeVisible();
  });

  test('window toggle updates URL search param', async ({ page }) => {
    await page.goto('/portfolio/rollup');
    await expect(page.getByTestId('rollup-curve-chart')).toBeVisible({
      timeout: 10_000,
    });
    await page.getByTestId('rollup-curve-window-30d').click();
    await expect(page).toHaveURL(/[?&]window=30d/);
  });

  test('exposure-row click opens the drill drawer', async ({ page }) => {
    await page.goto('/portfolio/rollup');
    await expect(page.getByTestId('rollup-exposure-list')).toBeVisible({
      timeout: 10_000,
    });
    // Find ANY clickable exposure row; the rollup may have no asset classes
    // in test data, so this is a soft assertion gated on at least one row.
    const rows = page.locator('[data-testid^="rollup-exposure-row-"]');
    const count = await rows.count();
    test.skip(count === 0, 'No exposure rows in test data — drill click smoke skipped');
    await rows.first().click();
    await expect(page.getByTestId('rollup-drill-drawer')).toBeVisible();
    await expect(page.getByTestId('rollup-drill-title')).toBeVisible();
  });
});
