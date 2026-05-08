import type { ConsoleMessage } from '@playwright/test';
import { expect, gotoChart, test } from './fixtures';

const perfCanonicalId = process.env.E2E_PERF_CANONICAL_ID ?? process.env.E2E_ACTIVE_CANONICAL_ID ?? 'AAPL.US';

function percentile(values: number[], percentileRank: number): number {
  if (values.length === 0) return Number.POSITIVE_INFINITY;
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.ceil((percentileRank / 100) * sorted.length) - 1;
  return sorted[Math.max(0, Math.min(sorted.length - 1, index))] ?? Number.POSITIVE_INFINITY;
}

test.describe('Phase 9 frontend perf smoke gates', () => {
  test('live-tail tick-to-render p95 is under 250ms', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 50): TradeChart must emit console.log('[perf] tick latency=', ms) from a test fixture tick.
    const latencies: number[] = [];
    authedPage.on('console', (message: ConsoleMessage) => {
      if (message.type() !== 'log') return;
      const text = message.text();
      const match = /^\[perf\] tick latency=\s*(\d+(?:\.\d+)?)$/.exec(text);
      if (match?.[1]) latencies.push(Number(match[1]));
    });

    await gotoChart(authedPage, perfCanonicalId);
    await expect(authedPage.getByTestId('trade-chart')).toBeVisible({ timeout: 5_000 });
    await authedPage.waitForTimeout(30_000);

    const p95 = percentile(latencies, 95);
    expect(p95, `live-tail render p95=${p95.toFixed(1)}ms`).toBeLessThanOrEqual(250);
  });

  test('initial chart render completes within 2s', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 50): add bars-rendered marker in TradeChart after klinecharts receives the initial page.
    const start = await authedPage.evaluate(() => performance.now());
    await gotoChart(authedPage, perfCanonicalId);
    await expect(authedPage.locator('[data-chart-container]')).toBeVisible({ timeout: 5_000 });
    await expect(authedPage.getByTestId('bars-rendered')).toBeVisible({ timeout: 2_000 });
    const elapsed = await authedPage.evaluate((startedAt) => performance.now() - startedAt, start);

    expect(elapsed, `initial chart render=${elapsed.toFixed(1)}ms`).toBeLessThanOrEqual(2_000);
  });
});
