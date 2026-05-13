import { expect, test } from './fixtures';

test.describe('phase 11a-D admin AI page smoke', () => {
  test('all 4 sub-panels render on /admin/ai', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures (admin role + seeded ai_router config)');
    await authedPage.goto('/admin/ai');
    await expect(authedPage.getByText(/capability map editor/i)).toBeVisible();
    await expect(authedPage.getByText(/provider key CRUD/i)).toBeVisible();
    await expect(authedPage.getByText(/cost ledger/i).first()).toBeVisible();
    await expect(authedPage.getByText(/heavy-box state/i).first()).toBeVisible();
  });

  test('capability map save mints + consumes csrf nonce', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    await authedPage.goto('/admin/ai');
    const csrfRequest = authedPage.waitForRequest((r) => r.url().endsWith('/api/admin/csrf/issue'));
    const putRequest = authedPage.waitForRequest((r) => r.url().endsWith('/api/admin/config/ai_router/capability_map') && r.method() === 'PUT');
    await authedPage.getByLabel('Capability map JSON').fill('{"CODING":[{"provider":"ollama-nuc","model":"qwen3-coder"}]}');
    await authedPage.getByRole('button', { name: /save/i }).click();
    await csrfRequest;
    const put = await putRequest;
    expect(put.headers()['x-csrf-nonce']).toBeTruthy();
  });
});
