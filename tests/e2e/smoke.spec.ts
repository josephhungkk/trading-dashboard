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

  test('cmd+k opens palette and / prefix navigates', async ({ page }) => {
    await page.goto('/overview');
    // Wait for the SPA + global keydown listener to mount before typing.
    await page.waitForLoadState('networkidle');
    await page.keyboard.press('Meta+k');
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
