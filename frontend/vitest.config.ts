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
  },
});
