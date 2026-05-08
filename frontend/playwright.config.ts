import { defineConfig, devices } from '@playwright/test';

// TODO(Task 49+50): add @playwright/test as a direct devDependency.
// The frontend currently has playwright via Vitest browser support, but the
// Playwright test runner APIs used by this config live in @playwright/test.
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: true,
  retries: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'iphone-se', use: { ...devices['iPhone SE'] } },
  ],
});
