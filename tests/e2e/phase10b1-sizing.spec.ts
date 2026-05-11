import { expect, test } from '@playwright/test';

/**
 * Phase 10b.1 E3 — position-sizing E2E smoke.
 *
 * Covers two surfaces:
 *  1. /trade/sizing page-render smoke (mounts without crashing, 3 columns
 *     visible, shared inputs visible).
 *  2. Sizing-defaults round-trip on the admin endpoints — GET then PUT then
 *     GET again, asserting the persisted shape comes back. Mirrors the
 *     CRUD pattern in phase10a-risk.spec.ts.
 *
 * Deep verdict / warn / block behavior is covered by Vitest component
 * tests (TradeTicketModal.test.tsx + usePositionSizing.test.tsx) which
 * mock the API responses. Playwright against a live broker layer is too
 * fragile (depends on operator-provisioned NLV + bars_1d data).
 */

async function mintNonce(
  request: import('@playwright/test').APIRequestContext,
): Promise<string> {
  const resp = await request.post('/api/admin/csrf/issue');
  expect(resp.ok()).toBeTruthy();
  const body = (await resp.json()) as { nonce: string };
  return body.nonce;
}

test.describe('Phase 10b.1 — position-sizing', () => {
  test('@smoke /trade/sizing page mounts with 3 columns', async ({ page }) => {
    await page.goto('/trade/sizing');
    await expect(page.getByTestId('column-fixed_fractional')).toBeVisible();
    await expect(page.getByTestId('column-risk_per_trade')).toBeVisible();
    await expect(page.getByTestId('column-vol_targeted')).toBeVisible();
    // Shared inputs at the top.
    await expect(page.getByTestId('page-account-id')).toBeVisible();
    await expect(page.getByTestId('page-instrument-id')).toBeVisible();
    await expect(page.getByTestId('page-side')).toBeVisible();
    await expect(page.getByTestId('page-entry')).toBeVisible();
  });

  test('admin sizing-defaults GET returns default shape', async ({ request }) => {
    const accountId = '00000000-0000-0000-0000-000000000099';
    const resp = await request.get(
      `/api/risk/sizing-defaults/${accountId}`,
    );
    expect(resp.ok()).toBeTruthy();
    const body = (await resp.json()) as {
      method: string;
      fixed_fractional_risk_pct: string;
      risk_per_trade_risk_pct: string;
      vol_targeted_target_vol_pct: string;
    };
    expect(body.method).toBe('fixed_fractional');
    expect(body.fixed_fractional_risk_pct).toBeTruthy();
  });

  test('admin sizing-defaults PUT requires CSRF nonce', async ({ request }) => {
    const accountId = '00000000-0000-0000-0000-000000000099';
    const resp = await request.put(
      `/api/admin/sizing-defaults/${accountId}`,
      {
        data: {
          method: 'risk_per_trade',
          fixed_fractional_risk_pct: '2.00',
          risk_per_trade_risk_pct: '1.50',
          vol_targeted_target_vol_pct: '15.00',
        },
      },
    );
    // Without nonce — 401/403/422.
    expect([401, 403, 422]).toContain(resp.status());
  });

  test('admin sizing-defaults PUT with CSRF round-trips', async ({ request }) => {
    const accountId = '00000000-0000-0000-0000-000000000099';
    const nonce = await mintNonce(request);
    const putResp = await request.put(
      `/api/admin/sizing-defaults/${accountId}`,
      {
        headers: { 'X-Confirm-Nonce': nonce },
        data: {
          method: 'risk_per_trade',
          fixed_fractional_risk_pct: '2.00',
          risk_per_trade_risk_pct: '1.50',
          vol_targeted_target_vol_pct: '15.00',
        },
      },
    );
    expect(putResp.status()).toBe(204);

    const getResp = await request.get(
      `/api/risk/sizing-defaults/${accountId}`,
    );
    expect(getResp.ok()).toBeTruthy();
    const body = (await getResp.json()) as { method: string };
    expect(body.method).toBe('risk_per_trade');
  });
});
