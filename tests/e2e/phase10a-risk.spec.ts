import { test, expect } from '@playwright/test';

/**
 * Phase 10a.5.1 C3 — Risk-gate E2E specs.
 *
 * Covers the four flows called out in the cleanup plan:
 *  1. admin-risk-crud      — POST/PUT/DELETE /api/admin/risk-limits
 *  2. kill-switch          — POST /api/admin/accounts/{id}/kill-switch
 *  3. risk-warn DOM        — /admin/risk page renders without crashing
 *  4. risk-block DOM       — /admin/risk/decisions page renders without crashing
 *
 * The two render specs are smoke-shape checks against the public URL — they
 * confirm the routes resolve, the page mounts, and the data-fetching hooks
 * don't throw on first paint. Deep WARN/BLOCK banner behavior is covered by
 * Vitest component tests (TradeTicketModal.test.tsx) which can mock the
 * preview response shape; reproducing that in Playwright against a live
 * broker layer is too fragile because it depends on operator-provisioned
 * paper accounts with specific positions.
 *
 * The CRUD specs use the admin API directly (service-token auth via the
 * CF-Access-Client-* env vars in playwright.config.ts) — the same pattern
 * used by the Phase 2 admin config round-trip in smoke.spec.ts.
 */

async function mintNonce(request: import('@playwright/test').APIRequestContext): Promise<string> {
  const resp = await request.post('/api/admin/csrf/issue');
  expect(resp.status()).toBe(200);
  return (await resp.json()).nonce;
}

test.describe('Phase 10a.5.1 risk-gate admin API', () => {
  test('risk-limits CRUD round-trip via admin API', async ({ request }) => {
    const scope = `e2e_phase10a_${Date.now()}`;
    const nonce = await mintNonce(request);

    // CREATE
    const createResp = await request.post('/api/admin/risk-limits', {
      headers: { 'X-Confirm-Nonce': nonce },
      data: {
        scope_kind: 'account',
        scope_value: scope,
        check_kind: 'max_daily_loss',
        limit_value: '500.00',
        unit: 'USD',
        active: true,
      },
    });
    if (createResp.status() === 401 || createResp.status() === 403) {
      test.skip(true, 'admin auth not available for this E2E run');
    }
    if (createResp.status() === 503) {
      test.skip(true, 'backend admin layer not yet configured');
    }
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    expect(created).toHaveProperty('id');
    const limitId: string = created.id;

    // UPDATE
    const updateNonce = await mintNonce(request);
    const updateResp = await request.put(`/api/admin/risk-limits/${limitId}`, {
      headers: { 'X-Confirm-Nonce': updateNonce },
      data: { limit_value: '750.00', active: true },
    });
    expect(updateResp.status()).toBe(200);
    expect((await updateResp.json()).limit_value).toBe('750.00');

    // DELETE (soft)
    const deleteNonce = await mintNonce(request);
    const deleteResp = await request.delete(`/api/admin/risk-limits/${limitId}`, {
      headers: { 'X-Confirm-Nonce': deleteNonce },
    });
    expect(deleteResp.status()).toBe(204);
  });

  test('reject risk-limits POST without nonce', async ({ request }) => {
    const resp = await request.post('/api/admin/risk-limits', {
      data: {
        scope_kind: 'account',
        scope_value: 'nonce-missing-test',
        check_kind: 'max_daily_loss',
        limit_value: '100.00',
        unit: 'USD',
        active: true,
      },
    });
    // 422 (FastAPI dep rejection) or 403 (csrf reject) — both prove the
    // CSRF check is wired. 401 means admin auth came first which also OK.
    if (resp.status() === 503) test.skip(true, 'backend admin layer not yet configured');
    expect([401, 403, 422]).toContain(resp.status());
  });

  test('GET /api/risk/limits returns array (possibly empty)', async ({ request }) => {
    const resp = await request.get('/api/risk/limits');
    if (resp.status() === 401 || resp.status() === 403) {
      test.skip(true, 'admin auth not available for this E2E run');
    }
    if (resp.status() === 503) {
      test.skip(true, 'backend admin layer not yet configured');
    }
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.limits)).toBe(true);
  });

  test('GET /api/risk/decisions returns paginated envelope', async ({ request }) => {
    const resp = await request.get('/api/risk/decisions?limit=10');
    if (resp.status() === 401 || resp.status() === 403) {
      test.skip(true, 'admin auth not available for this E2E run');
    }
    if (resp.status() === 503) {
      test.skip(true, 'backend admin layer not yet configured');
    }
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.decisions)).toBe(true);
  });
});

test.describe('Phase 10a.5.1 kill-switch admin API', () => {
  test('kill-switch UPSERT round-trip on a real account', async ({ request }) => {
    const listResp = await request.get('/api/accounts');
    if (listResp.status() === 503) test.skip(true, 'broker layer not yet provisioned');
    if (listResp.status() === 401 || listResp.status() === 403) {
      test.skip(true, 'admin auth not available for this E2E run');
    }
    const list = await listResp.json();
    if (!list.accounts || list.accounts.length === 0) {
      test.skip(true, 'no broker accounts present in DB');
    }
    // Prefer a paper account so a stray kill-switch flip can't impede live trading.
    const target = list.accounts.find((a: { mode: string }) => a.mode === 'paper')
      ?? list.accounts[0];
    const accountId: string = target.id;

    // Enable
    const enableNonce = await mintNonce(request);
    const enableResp = await request.post(
      `/api/admin/accounts/${accountId}/kill-switch`,
      {
        headers: { 'X-Confirm-Nonce': enableNonce },
        data: { enabled: true, reason: 'e2e test toggle' },
      },
    );
    expect(enableResp.status()).toBe(200);
    expect((await enableResp.json()).enabled).toBe(true);

    // Disable (clean up so we don't leave the account blocked)
    const disableNonce = await mintNonce(request);
    const disableResp = await request.post(
      `/api/admin/accounts/${accountId}/kill-switch`,
      {
        headers: { 'X-Confirm-Nonce': disableNonce },
        data: { enabled: false, reason: 'e2e test cleanup' },
      },
    );
    expect(disableResp.status()).toBe(200);
    expect((await disableResp.json()).enabled).toBe(false);
  });
});

test.describe('Phase 10a.5.1 risk pages render smoke', () => {
  test('/admin/risk loads without runtime error', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    await page.goto('/admin/risk');
    await page.waitForLoadState('networkidle');

    // The page should mount its h1/heading. We don't pin the exact text
    // because Phase 10b may rename — just confirm a heading exists.
    await expect(page.getByRole('heading').first()).toBeVisible();

    // No unhandled exceptions from data hooks (useRiskLimits etc.)
    expect(errors).toEqual([]);
  });

  test('/admin/risk/decisions loads without runtime error', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    await page.goto('/admin/risk/decisions');
    await page.waitForLoadState('networkidle');

    await expect(page.getByRole('heading').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('/admin/accounts loads without runtime error', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    await page.goto('/admin/accounts');
    await page.waitForLoadState('networkidle');

    await expect(page.getByRole('heading').first()).toBeVisible();
    expect(errors).toEqual([]);
  });
});
