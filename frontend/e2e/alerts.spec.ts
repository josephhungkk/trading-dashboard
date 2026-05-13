import { expect, test } from '@playwright/test';

test.fixme(true, 'wire after docker-compose harness lands (phase 9.5+ playwright debt)');

test('create-rule golden path: NL → parse → confirm → list', async ({ page }) => {
  await page.goto('/alerts');
  await page.getByRole('button', { name: /New Alert/i }).click();
  await page.getByLabel(/Label/i).fill('AAPL > 200');
  await page.getByLabel(/Rule text/i).fill('alert me when AAPL crosses above 200');
  await page.getByRole('button', { name: /Parse/i }).click();
  await expect(page.getByTestId('create-alert-parsed')).toBeVisible();
  await page.getByRole('button', { name: /Confirm/i }).click();
  await expect(page.getByText(/AAPL > 200/)).toBeVisible();
});

test('parse_failed → JSON editor opens with suggestions', async ({ page }) => {
  await page.goto('/alerts');
  await page.getByRole('button', { name: /New Alert/i }).click();
  await page.getByLabel(/Label/i).fill('unparseable');
  await page.getByLabel(/Rule text/i).fill('asdfqwer not a rule');
  await page.getByRole('button', { name: /Parse/i }).click();
  await expect(page.getByTestId('parse-failed-editor')).toBeVisible();
  await expect(page.getByTestId('predicate-json-textarea')).toBeVisible();
});
