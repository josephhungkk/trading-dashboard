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
    await expect(page.locator('text=/Backend:/')).toBeVisible();
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
