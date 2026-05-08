import path from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Phase 0: unit tests only (jsdom). Storybook's addon-vitest browser-mode
// story tests are disabled here because they require Playwright chromium,
// which is deferred to Phase 5+ per the spec. Story rendering is still
// verified in CI via `pnpm build-storybook`.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    coverage: { reporter: ['text', 'html'] },
    // Phase 9.6: Playwright e2e specs live under e2e/ and import
    // @playwright/test, which is not in the Vitest dep tree. Vitest
    // would otherwise collect them via its default include glob and
    // fail with "Failed to resolve import @playwright/test". Exclude
    // here so only Vitest unit/integration tests run; Playwright
    // owns the e2e/ directory via its own runner.
    exclude: ['e2e/**', 'node_modules/**', 'dist/**', '.idea/**', '.git/**', '.cache/**'],
  },
});
