import type { Page } from '@playwright/test';
import { test as base } from '@playwright/test';

interface E2EFixtures {
  authedPage: Page;
}

// TODO(Task 49+50): add @playwright/test as a direct devDependency.
// Playwright tests use the runner API from @playwright/test; the repo currently
// lists only playwright directly.
export const test = base.extend<E2EFixtures>({
  authedPage: async ({ page }, run) => {
    await page.context().setExtraHTTPHeaders({
      'CF-Access-Jwt-Assertion': process.env.E2E_JWT ?? 'test-bypass',
    });
    await page.context().addCookies([{
      name: 'cf_authorization',
      value: process.env.E2E_JWT ?? 'test-bypass',
      domain: 'localhost',
      path: '/',
    }]);
    await run(page);
  },
});

export { expect } from '@playwright/test';

export async function gotoChart(page: Page, canonicalId: string): Promise<void> {
  await page.goto(`/chart/${encodeURIComponent(canonicalId)}`);
}
