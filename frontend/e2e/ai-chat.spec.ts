import { expect, test } from './fixtures';

test.describe('phase 11a-D AI chat smoke', () => {
  test('chat page renders + send a message + receive a reply', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures (litellm container + ws routing)');
    await authedPage.goto('/ai/chat');
    await expect(authedPage.getByLabel('Message')).toBeVisible();
    await authedPage.getByLabel('Message').fill('What is the current spot price of AAPL?');
    await authedPage.getByRole('button', { name: 'Send' }).click();
    // Expect at least one assistant message to materialise.
    await expect(authedPage.locator('article').filter({ hasText: 'Assistant' }).first()).toBeVisible({ timeout: 30_000 });
  });

  test('rate-limited badge appears when 5+ turns hit within 60s', async ({ authedPage }) => {
    test.fixme(true, 'requires compose+fixtures');
    await authedPage.goto('/ai/chat');
    // Send 5 quick turns then attempt a 6th.
    for (let i = 0; i < 6; i += 1) {
      await authedPage.getByLabel('Message').fill(`turn ${i}`);
      await authedPage.getByRole('button', { name: 'Send' }).click();
    }
    await expect(authedPage.getByText(/wait.*5.*min/i)).toBeVisible({ timeout: 5_000 });
  });
});
