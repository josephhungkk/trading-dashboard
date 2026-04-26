import { test, expect } from '@playwright/test';

test.describe('Phase 1 smoke', () => {
  test('GET /health returns db:ok in prod', async ({ request }) => {
    const resp = await request.get('/health');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.status).toBe('ok');
    expect(body.env).toBe('prod');
    expect(body.db).toBe('ok');
  });

  test('root page has correct title', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle('Trading Dashboard');
  });

  test('security headers present on /', async ({ request }) => {
    const resp = await request.get('/');
    const h = resp.headers();
    expect(h['strict-transport-security']).toContain('max-age=');
    expect(h['x-frame-options']).toBe('DENY');
    expect(h['x-content-type-options']).toBe('nosniff');
    expect(h['x-robots-tag']).toContain('noindex');
    expect(h['referrer-policy']).toBe('no-referrer');
    expect(h['content-security-policy']).toContain("default-src 'self'");
  });

  test('unauthenticated requests are blocked at CF Access', async ({ baseURL }) => {
    // Use Node fetch with redirect:'manual' — Playwright's APIRequestContext
    // silently follows the CF 302 to the login page and surfaces 200, masking the gate.
    const resp = await fetch(`${baseURL}/health`, { redirect: 'manual' });
    expect([302, 401, 403]).toContain(resp.status);
  });

  test('admin config round-trip via service token', async ({ request }) => {
    const ns = 'test';
    const key = `phase2_smoke_${Date.now()}`;
    const postResp = await request.post(`/api/admin/config`, {
      data: { namespace: ns, key, value: 'ok', value_type: 'str' },
    });
    expect(postResp.status()).toBe(201);

    const getResp = await request.get(`/api/admin/config/${ns}/${key}`);
    expect(getResp.status()).toBe(200);
    expect((await getResp.json()).value).toBe('ok');

    const delResp = await request.delete(`/api/admin/config/${ns}/${key}`);
    expect(delResp.status()).toBe(204);
  });

  test('admin secret reveal via service token', async ({ request }) => {
    const ns = 'test';
    const key = `phase2_secret_${Date.now()}`;
    const postResp = await request.post(`/api/admin/secrets`, {
      data: { namespace: ns, key, value: 's3cr3t-value', value_type: 'str' },
    });
    expect(postResp.status()).toBe(201);

    const revealResp = await request.post(
      `/api/admin/secrets/${ns}/${key}/reveal`,
    );
    expect(revealResp.status()).toBe(200);
    expect((await revealResp.json()).value).toBe('s3cr3t-value');
    expect(revealResp.headers()['cache-control']).toContain('no-store');

    const delResp = await request.delete(`/api/admin/secrets/${ns}/${key}`);
    expect(delResp.status()).toBe(204);
  });
});

test.describe('Phase 3 frontend shell', () => {
  test('loads in paper mode by default', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('body[data-mode="paper"]')).toBeAttached();
  });

  test('paper→live shows confirm dialog; cancel returns to paper', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('switch', { name: /mode/i }).click();
    await expect(page.getByRole('dialog', { name: /switch to live/i })).toBeVisible();
    await page.getByRole('button', { name: /^cancel$/i }).click();
    await expect(page.locator('body[data-mode="paper"]')).toBeAttached();
  });

  test('command palette opens via topbar trigger and / prefix navigates', async ({ page }) => {
    await page.goto('/overview');
    await page.waitForLoadState('networkidle');
    // Open via the topbar trigger button. Match on the visible "⌘K" label
    // (more distinctive in prod than the aria-label, which our Button
    // primitive may not surface as the computed accessible name through
    // the icon + span children).
    await page.locator('header button:has-text("⌘K")').first().click();
    // cmdk's Command.Dialog renders with visibility:hidden until first measure,
    // so toBeVisible() flakes; assert DOM presence + open state instead.
    const dialog = page.getByRole('dialog', { name: /command palette/i });
    await expect(dialog).toBeAttached();
    await expect(dialog).toHaveAttribute('data-state', 'open');
    await page.keyboard.type('/orders');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/orders/);
  });

  test('watchlist column customizer opens and applies', async ({ page }) => {
    await page.goto('/watchlist');
    await page.waitForLoadState('networkidle');
    // findByRole-equivalent — explicit toBeVisible wait so we don't race the
    // WatchlistsService default-watchlist hydration that renders the toolbar.
    const customizeBtn = page.getByRole('button', { name: /customize columns/i });
    await expect(customizeBtn).toBeVisible();
    await customizeBtn.click();
    await expect(page.getByRole('dialog', { name: /customize columns/i })).toBeVisible();
    await page.getByRole('button', { name: /^apply$/i }).click();
    await expect(page.getByRole('dialog', { name: /customize columns/i })).not.toBeVisible();
  });

  test('mobile viewport renders BottomTabBar + navigates to positions', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/overview');
    await page.waitForLoadState('networkidle');
    // BottomTabBar uses `md:hidden` — let the mobile media-query layout settle
    // before asserting visibility.
    const tablist = page.getByRole('tablist', { name: /primary/i });
    await expect(tablist).toBeVisible();
    await page.getByRole('tab', { name: /positions/i }).click();
    await expect(page).toHaveURL(/\/positions/);
  });
});

test.describe('Phase 4 broker accounts', () => {
  // The broker layer is operator-provisioned on the NUC (mTLS provision +
  // sidecar Scheduled Tasks per plan §49.4). Until that runs, the lifespan
  // skips broker_registry init and /api/accounts returns 503 "broker layer
  // not yet configured". CI smoke runs ahead of that, so skip the suite
  // gracefully when we see the unconfigured 503 envelope.
  test('GET /api/accounts returns AccountListResponse without internal fields', async ({ request }) => {
    const r = await request.get('/api/accounts');
    if (r.status() === 503) {
      const body = await r.json();
      const detail = body.detail ?? body.error ?? '';
      if (typeof detail === 'string' && detail.includes('broker layer not yet configured')) {
        test.skip(true, 'broker layer not yet provisioned (operator step §49.4)');
      }
    }
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.accounts)).toBe(true);
    expect(body).toHaveProperty('degraded_sidecars');
    expect(Array.isArray(body.degraded_sidecars)).toBe(true);
    for (const acc of body.accounts) {
      expect(acc).not.toHaveProperty('gateway_label');
      expect(acc).not.toHaveProperty('account_number');
      expect(typeof acc.id).toBe('string');
      expect(['ibkr', 'futu', 'schwab']).toContain(acc.broker_id);
      expect(['live', 'paper']).toContain(acc.mode);
    }
  });

  test('GET /api/accounts/{id}/positions returns proto-shaped JSON with Decimal-string Money', async ({ request }) => {
    const listResp = await request.get('/api/accounts');
    if (listResp.status() === 503) test.skip(true, 'broker layer not yet provisioned');
    const list = await listResp.json();
    if (!list.accounts || list.accounts.length === 0) test.skip(true, 'no broker accounts present in DB');
    const id = list.accounts[0].id;
    const r = await request.get(`/api/accounts/${id}/positions`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body)).toBe(true);
    for (const pos of body) {
      expect(typeof pos.avg_cost.value).toBe('string');
      expect(typeof pos.avg_cost.currency).toBe('string');
      expect(pos.avg_cost.currency).toMatch(/^[A-Z]{3}$/);
    }
  });

  test('GET /api/accounts/{id}/summary returns Money with currency', async ({ request }) => {
    const listResp = await request.get('/api/accounts');
    if (listResp.status() === 503) test.skip(true, 'broker layer not yet provisioned');
    const list = await listResp.json();
    if (!list.accounts || list.accounts.length === 0) test.skip(true, 'no broker accounts present in DB');
    const id = list.accounts[0].id;
    const r = await request.get(`/api/accounts/${id}/summary`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.net_liquidation.currency).toMatch(/^[A-Z]{3}$/);
    expect(typeof body.net_liquidation.value).toBe('string');
  });

  test('GET /api/accounts/{id}/orders returns array (possibly empty)', async ({ request }) => {
    const listResp = await request.get('/api/accounts');
    if (listResp.status() === 503) test.skip(true, 'broker layer not yet provisioned');
    const list = await listResp.json();
    if (!list.accounts || list.accounts.length === 0) test.skip(true, 'no broker accounts present in DB');
    const id = list.accounts[0].id;
    const r = await request.get(`/api/accounts/${id}/orders`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body)).toBe(true);
  });
});
