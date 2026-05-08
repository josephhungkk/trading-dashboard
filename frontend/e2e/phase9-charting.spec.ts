import { expect, gotoChart, test } from './fixtures';

const activeSetCanonicalId = process.env.E2E_ACTIVE_CANONICAL_ID ?? 'AAPL.US';
const coldCanonicalId = process.env.E2E_COLD_CANONICAL_ID ?? 'ILLQ.US';
const positionedCanonicalId = process.env.E2E_POSITION_CANONICAL_ID ?? activeSetCanonicalId;

test.describe('Phase 9 charting golden flows', () => {
  test('active-set load persists RSI across refresh', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 49): fixture active-set symbol, seeded bars, chart_layouts API, and persisted RSI layout.
    await gotoChart(authedPage, activeSetCanonicalId);
    await expect(authedPage.locator('[data-chart-container]')).toBeVisible({ timeout: 5_000 });
    await expect(authedPage.getByTestId('trade-chart')).toBeVisible({ timeout: 2_000 });

    // TODO(Task 49+1): add data-testid to ChartToolbar.tsx Indicators button.
    await authedPage.getByRole('button', { name: 'Indicators' }).click();
    await authedPage.getByRole('checkbox', { name: 'RSI' }).check();
    await authedPage.getByRole('button', { name: 'Apply' }).click();
    await expect(authedPage.getByText('RSI', { exact: true })).toBeVisible({ timeout: 5_000 });

    await authedPage.reload();
    await expect(authedPage.locator('[data-chart-container]')).toBeVisible({ timeout: 5_000 });
    await expect(authedPage.getByText('RSI', { exact: true })).toBeVisible({ timeout: 5_000 });
  });

  test('cold-symbol backfill renders bars and then live tail updates', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 49): fixture cold symbol, backfill-capable broker sidecar, seeded auth, and live-tail feed.
    const barsResponses: string[] = [];
    authedPage.on('response', (response) => {
      if (response.url().includes('/api/bars')) barsResponses.push(response.url());
    });

    await gotoChart(authedPage, coldCanonicalId);
    await expect(authedPage.locator('[data-chart-container]')).toBeVisible({ timeout: 5_000 });
    await expect(authedPage.getByTestId('trade-chart')).toBeVisible({ timeout: 5_000 });
    await expect.poll(() => barsResponses.length, { timeout: 5_000 }).toBeGreaterThan(0);
    // TODO(Task 49+1): add bars-rendered marker from TradeChart after DataLoader callback receives data.
    await expect(authedPage.getByTestId('bars-rendered')).toBeVisible({ timeout: 5_000 });
    // TODO(Task 49+1): add live-tail-rendered marker or event counter exposed by TradeChart.
    await expect(authedPage.getByTestId('live-tail-rendered')).toBeVisible({ timeout: 10_000 });
  });

  test('cursor pagination prepends once and cache avoids duplicate range fetch', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 49): fixture at least 6 months of 1m bars and expose DataLoader prepend/cache observability.
    const matchingBarsRequests: string[] = [];
    authedPage.on('request', (request) => {
      const url = request.url();
      if (url.includes('/api/bars') && url.includes('cursor=')) matchingBarsRequests.push(url);
    });

    await gotoChart(authedPage, activeSetCanonicalId);
    await expect(authedPage.getByTestId('trade-chart')).toBeVisible({ timeout: 5_000 });
    // TODO(Task 49+1): add stable chart viewport control or klinecharts test hook for scrolling 6 months back.
    await authedPage.mouse.wheel(-20_000, 0);
    await expect.poll(() => matchingBarsRequests.length, { timeout: 5_000 }).toBeGreaterThan(0);
    const firstRequestCount = matchingBarsRequests.length;

    await authedPage.mouse.wheel(20_000, 0);
    await authedPage.mouse.wheel(-20_000, 0);
    await expect.poll(() => matchingBarsRequests.length).toBe(firstRequestCount);
  });

  test('dragging SL on open position confirms ModifyOrder and moves handle', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 49): fixture open bracket position with visible SL overlay, ModifyOrder stub, and toast capture.
    await gotoChart(authedPage, positionedCanonicalId);
    await expect(authedPage.locator('[data-chart-container]')).toBeVisible({ timeout: 5_000 });
    // TODO(Task 49+1): add data-testid to PositionOverlay SL handle.
    const stopLossHandle = authedPage.getByTestId('position-sl-handle').first();
    await expect(stopLossHandle).toBeVisible({ timeout: 5_000 });

    const box = await stopLossHandle.boundingBox();
    expect(box).not.toBeNull();
    if (box === null) return;

    await authedPage.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await authedPage.mouse.down();
    await authedPage.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 40);
    await authedPage.mouse.up();

    await expect(authedPage.getByRole('dialog', { name: /confirm/i })).toBeVisible({ timeout: 5_000 });
    await authedPage.getByRole('button', { name: /confirm/i }).click();
    // TODO(Task 49+1): add data-testid to success toast and updated SL handle price label.
    await expect(authedPage.getByText(/modified|saved|success/i)).toBeVisible({ timeout: 5_000 });
    await expect(stopLossHandle).toHaveAttribute('data-price-state', 'updated');
  });

  test('mobile iPhone SE chart toolbar and gestures are usable', async ({ authedPage }, testInfo) => {
    test.fixme(testInfo.project.name !== 'iphone-se', 'iPhone SE coverage runs only in the iphone-se project');
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 49): fixture mobile-accessible chart data and add gesture/fullscreen observability markers.
    await gotoChart(authedPage, activeSetCanonicalId);
    await expect(authedPage.locator('[data-chart-container]')).toBeVisible({ timeout: 5_000 });
    await expect(authedPage.getByTestId('trade-chart')).toBeVisible({ timeout: 5_000 });

    const compactToolbar = authedPage.getByTestId('chart-toolbar-compact');
    await expect(compactToolbar).toBeVisible({ timeout: 5_000 });
    await expect(compactToolbar.getByRole('button')).toHaveCount(5);
    await expect(compactToolbar.getByRole('button', { name: 'More options' })).toBeVisible();
    await expect(compactToolbar.getByRole('button', { name: 'Fullscreen' })).toBeVisible();

    // TODO(Task 49+1): add a klinecharts viewport-scale marker to assert pinch zoom changed range.
    await authedPage.touchscreen.tap(188, 334);
    await expect(authedPage.getByRole('button', { name: 'Fullscreen' })).toBeVisible();
  });

  test('aggregator crash recovery leaves no bar gaps', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    // TODO(Task 49): fixture compose service controls, Postgres WAL assertions, aggregator restart, and gap query.
    await gotoChart(authedPage, activeSetCanonicalId);
    await expect(authedPage.getByTestId('trade-chart')).toBeVisible({ timeout: 5_000 });
    // TODO(Task 49+1): add a test-only backend endpoint or CI helper to kill/restart bar_aggregator safely.
    await authedPage.request.post('/api/test/bar-aggregator/kill');
    await expect(authedPage.getByText(/ticks queued/i)).toBeVisible({ timeout: 10_000 });
    await authedPage.request.post('/api/test/bar-aggregator/restart');
    await expect(authedPage.getByText(/no bar gaps/i)).toBeVisible({ timeout: 10_000 });
  });
});
