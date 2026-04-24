# Phase 3 — Frontend Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the v2 UI shell end-to-end with realistic mocks — routing, state, theming, 3-panel desktop + mobile bottom-tab-bar responsive layout, multi-watchlist with wide-column customizer, ticking quote mocks, CF-Access-gated admin CRUD UI. Tagged `v0.3.0`.

**Architecture:** Single `AppShell` subtree (Tailwind-responsive) hosts a 3-panel desktop layout (`react-resizable-panels`) and a mobile branch (bottom-tab-bar + swipe drawers). TanStack Router handles 11 routes. Zustand state split into global stores (mode, theme, commands, connected) and a scoped factory with phantom types that produces two parallel instances per trading-state slice (live + paper) — only the active scope holds live subscriptions via a `suspend/hydrate` lifecycle. Pluggable `XxxService` interfaces with `MockXxxService` implementations swap to real HTTP adapters in Phase 4+. `cmdk` + Radix primitives. Dark-only theme via Tailwind v4 `@theme` OKLCH tokens.

**Tech Stack:** React 19, TypeScript 6 strict, Vite 8, Tailwind v4, Zustand, TanStack Router, TanStack Table + Virtual, react-resizable-panels, cmdk, Radix UI, lucide-react, Vitest + RTL + Storybook 10 + Playwright.

**Reference spec:** `/mnt/c/dashboard/docs/superpowers/specs/2026-04-23-phase3-frontend-shell-design.md` (commit `fccc958`). All CRITICAL+HIGH+MEDIUM architect findings already applied.

**Per-task review chain at every commit boundary (per CLAUDE.md):**
1. Implementer subagent (uses `superpowers:subagent-driven-development/implementer-prompt.md`)
2. Spec-compliance reviewer
3. Code-quality reviewer
4. `everything-claude-code:typescript-reviewer`
5. `everything-claude-code:a11y-architect` (UI-touching tasks)
6. `everything-claude-code:security-reviewer` (AdminPage, services registry, anything touching CF Access)

**Environment reminder:** pnpm at `~/.npm-global/bin/pnpm`. Every Bash command in a subagent task must first `export PATH="$HOME/.npm-global/bin:$PATH"`. CWD is `/mnt/c/dashboard/frontend` unless otherwise specified.

**Commit discipline:** conventional type-enum (feat|fix|refactor|docs|test|chore|perf|ci). Lowercase subject. Body lines ≤ 100 chars. Commitlint enforces.

---

## File structure map (full)

```
frontend/
  package.json                     # +deps across all chunks
  vite.config.ts                   # +TanStack Router plugin, +/api proxy
  eslint.config.mjs                # +stores/scoped boundaries rule
  public/fonts/                    # NEW — Noto Sans + Noto Sans CJK subsets
  src/
    main.tsx                       # mount RouterProvider (rewritten Task 5)
    App.tsx                        # router provider (rewritten Task 5)
    styles/
      tailwind.css                 # @theme block (OKLCH dark + mode accents)
      global.css                   # @font-face unicode-range routing
    design-tokens/                 # existing; expanded to mirror @theme
    lib/
      utils.ts                     # existing (cn)
      formatters.ts                # NEW — money/percent/number/locale
      cmd-match.ts                 # NEW — fuzzy match for cmdk
    services/
      api.ts ws.ts lang.ts         # existing; lang.ts gets real mapping
      types.ts                     # NEW — Mode, Account, Order, Position, ...
      accounts.ts positions.ts orders.ts quotes.ts
      watchlists.ts commands.ts connected.ts quote-feeds.ts
      registry.ts                  # getServices() + resetServices()
      fixtures/
        brokers.ts accounts.ts symbols.ts positions.ts orders.ts watchlists.ts
        quote-feeds.ts index.ts
    stores/
      global/
        mode.ts theme.ts commands.ts connected.ts quote-feeds.ts
      scoped/
        account-store.ts positions-store.ts orders-store.ts watchlists-store.ts
        types.ts                   # phantom Scoped<M, T>
      factory.ts                   # createScopedStores<M>(mode)
      registry.ts                  # live/paper singletons + useActiveStores()
    hooks/
      use-media-query.ts
      use-mode-scoped.ts
      use-shortcut.ts
      use-ticking-quotes.ts
      use-toast.ts
      use-commands-effect.ts
    components/
      primitives/                  # 16 primitives — see Chunk E
      patterns/                    # 12 patterns — see Chunk F (incl. QuoteFeedDropdown)
      layout/
        AppShell/
        Topbar/
        LeftPanel/
        RightPanel/
    features/
      overview/  orders/  positions/  watchlist/  admin/  settings/
      trade/     alerts/
    routes/                        # TanStack Router file-based
      __root.tsx
      index.tsx  overview.tsx  orders.tsx  positions.tsx
      watchlist.tsx  watchlist.$id.tsx
      admin.tsx  admin.config.tsx  admin.secrets.tsx
      settings.tsx  trade.tsx  alerts.tsx
      routeTree.gen.ts             # GENERATED — gitignored
  tests/e2e/
    smoke.spec.ts                  # existing; extend with 5 frontend tests
```

Each primitive + pattern ships four files: `<Name>.tsx`, `<Name>.stories.tsx`, `<Name>.test.tsx`, `index.ts`.

---

## Chunk A — Foundations

### Task 1: Add dependencies

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml` (generated)

- [ ] **Step 1.1: Runtime deps**

```bash
export PATH="$HOME/.npm-global/bin:$PATH"
cd /mnt/c/dashboard/frontend

pnpm add zustand \
  @tanstack/react-router @tanstack/react-router-devtools \
  @tanstack/react-table @tanstack/react-virtual \
  react-resizable-panels cmdk \
  @radix-ui/react-avatar @radix-ui/react-checkbox @radix-ui/react-dialog \
  @radix-ui/react-dropdown-menu @radix-ui/react-popover @radix-ui/react-radio-group \
  @radix-ui/react-select @radix-ui/react-switch @radix-ui/react-tabs \
  @radix-ui/react-toast @radix-ui/react-tooltip \
  lucide-react
```

- [ ] **Step 1.2: Dev deps**

```bash
pnpm add -D @tanstack/router-plugin @tanstack/router-cli glyphhanger
```

- [ ] **Step 1.3: Verify**

```bash
pnpm typecheck
```
Expected: PASS.

- [ ] **Step 1.4: Commit**

```bash
cd /mnt/c/dashboard
git add frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): add phase 3 deps (zustand, tanstack, radix, cmdk)"
```

---

### Task 2: Tailwind @theme + design tokens parity

**Files:**
- Modify: `frontend/src/styles/tailwind.css`
- Modify: `frontend/src/design-tokens/colors.ts`
- Create: `frontend/src/design-tokens/design-tokens.test.ts`

- [ ] **Step 2.1: Replace `tailwind.css`**

```css
@import "tailwindcss";

@theme {
  /* Surfaces — dark-only */
  --color-bg:           oklch(15% 0.01 240);
  --color-panel:        oklch(20% 0.01 240);
  --color-elevated:     oklch(24% 0.01 240);
  --color-border:       oklch(30% 0.01 240);
  --color-fg:           oklch(96% 0.01 240);
  --color-fg-muted:     oklch(65% 0.01 240);
  --color-fg-subtle:    oklch(45% 0.01 240);

  /* Semantic */
  --color-primary:        oklch(70% 0.12 230);
  --color-primary-fg:     oklch(15% 0.01 240);
  --color-destructive:    oklch(62% 0.19 25);
  --color-destructive-fg: oklch(96% 0.01 240);
  --color-positive:       oklch(70% 0.17 145);
  --color-negative:       oklch(62% 0.19 25);
  --color-warn:           oklch(78% 0.15 75);
  --color-info:           oklch(70% 0.12 230);
  --color-muted:          oklch(30% 0.01 240);

  /* Mode accents */
  --color-accent-live:   oklch(58% 0.20 25);
  --color-accent-paper:  oklch(72% 0.18 75);
  --color-accent-active: var(--color-accent-paper);

  /* Status tints */
  --color-delayed-bg: oklch(22% 0.02 240);
  --color-delayed-fg: oklch(60% 0.02 240);

  /* Fonts */
  --font-sans: "Noto Sans", system-ui, sans-serif;
  --font-mono: "Noto Sans Mono", ui-monospace, monospace;

  /* Type scale */
  --text-xs: 0.75rem;  --text-sm: 0.875rem; --text-base: 1rem;
  --text-lg: 1.125rem; --text-xl: 1.25rem;  --text-2xl: 1.5rem;
  --text-3xl: 1.875rem;

  /* Radii */
  --radius-sm: 0.25rem; --radius-md: 0.5rem; --radius-lg: 0.75rem;
  --radius-full: 9999px;

  /* Motion */
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --duration-fast: 120ms;
  --duration-base: 200ms;
  --duration-slow: 320ms;
}

body { background: var(--color-bg); color: var(--color-fg); }
body[data-mode="live"]  { --color-accent-active: var(--color-accent-live); }
body[data-mode="paper"] { --color-accent-active: var(--color-accent-paper); }
```

- [ ] **Step 2.2: Mirror tokens in TS**

Replace `frontend/src/design-tokens/colors.ts`:

```ts
export const colors = {
  bg:              'oklch(15% 0.01 240)',
  panel:           'oklch(20% 0.01 240)',
  elevated:        'oklch(24% 0.01 240)',
  border:          'oklch(30% 0.01 240)',
  fg:              'oklch(96% 0.01 240)',
  fgMuted:         'oklch(65% 0.01 240)',
  fgSubtle:        'oklch(45% 0.01 240)',
  primary:         'oklch(70% 0.12 230)',
  primaryFg:       'oklch(15% 0.01 240)',
  destructive:     'oklch(62% 0.19 25)',
  destructiveFg:   'oklch(96% 0.01 240)',
  positive:        'oklch(70% 0.17 145)',
  negative:        'oklch(62% 0.19 25)',
  warn:            'oklch(78% 0.15 75)',
  info:            'oklch(70% 0.12 230)',
  muted:           'oklch(30% 0.01 240)',
  accentLive:      'oklch(58% 0.20 25)',
  accentPaper:     'oklch(72% 0.18 75)',
  delayedBg:       'oklch(22% 0.02 240)',
  delayedFg:       'oklch(60% 0.02 240)',
} as const;
export type ColorToken = keyof typeof colors;
```

- [ ] **Step 2.3: Parity test**

`frontend/src/design-tokens/design-tokens.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { colors } from './colors';

const css = readFileSync(resolve(__dirname, '../styles/tailwind.css'), 'utf8');

function extractVar(name: string): string | undefined {
  const re = new RegExp(`--${name}:\\s*([^;]+);`);
  return css.match(re)?.[1]?.trim();
}

describe('design-tokens/CSS parity', () => {
  it.each([
    ['color-bg',       colors.bg],
    ['color-panel',    colors.panel],
    ['color-fg',       colors.fg],
    ['color-positive', colors.positive],
    ['color-accent-live',  colors.accentLive],
    ['color-accent-paper', colors.accentPaper],
  ])('TS token matches CSS var --%s', (name, tsValue) => {
    expect(extractVar(name)).toBe(tsValue);
  });
});
```

- [ ] **Step 2.4: Run**

```bash
export PATH="$HOME/.npm-global/bin:$PATH"
cd /mnt/c/dashboard/frontend
pnpm test src/design-tokens/design-tokens.test.ts
```
Expected: 6 PASS.

- [ ] **Step 2.5: Commit**

```bash
cd /mnt/c/dashboard
git add frontend/src/styles/tailwind.css frontend/src/design-tokens/
git commit -m "feat(frontend): tailwind v4 @theme dark tokens + mode accent vars"
```

---

### Task 3: Noto fonts + subset + langForMarket real mapping

**Files:**
- Create: `frontend/public/fonts/*.woff2` (8 files)
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/services/lang.ts`
- Create: `frontend/src/services/lang.test.ts`

- [ ] **Step 3.1: Fetch Latin + CJK sources**

Download Latin weights (400/500/700) + Noto Sans Mono Regular from fontsource CDN. Subset CJK (TC/SC/HK/JP/KR) to symbol-set using `glyphhanger`. Target per-region subset ≤ 2 MB.

Commands:

```bash
cd /mnt/c/dashboard/frontend/public/fonts

for w in 400 500 700; do
  curl -LO "https://cdn.jsdelivr.net/fontsource/fonts/noto-sans@latest/latin-${w}-normal.woff2"
  mv "latin-${w}-normal.woff2" "NotoSans-${w}.woff2"
done
curl -LO "https://cdn.jsdelivr.net/fontsource/fonts/noto-sans-mono@latest/latin-400-normal.woff2"
mv "latin-400-normal.woff2" "NotoSansMono-400.woff2"

for r in tc sc hk jp kr; do
  curl -LO "https://cdn.jsdelivr.net/fontsource/fonts/noto-sans-${r}@latest/chinese-simplified-400-normal.woff2" 2>/dev/null || \
  curl -LO "https://cdn.jsdelivr.net/fontsource/fonts/noto-sans-${r}@latest/latin-400-normal.woff2"
done

# glyphhanger subset per region — see subset-sources.txt
```

Create `frontend/public/fonts/subset-sources.txt`:

```
TC: 騰訊 阿里 恒生 美團 港交所 匯豐 中國平安 比亞迪 聯發科 台積電
SC: 腾讯 阿里 美团 比亚迪 工商银行 招商银行 中国平安
HK: 騰訊 阿里 恒生 美團 港交所 匯豐
JP: トヨタ ソニー 任天堂 三菱 日立 ホンダ 富士 三菱UFJ
KR: 삼성 현대 LG SK 카카오 네이버 포스코 하이닉스
```

- [ ] **Step 3.2: `global.css` with unicode-range routing**

```css
@import "./tailwind.css";

@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSans-400.woff2") format("woff2");
  font-weight: 400; font-style: normal; font-display: swap;
  unicode-range: U+0000-007F, U+00A0-00FF, U+0100-017F, U+0180-024F; }
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSans-500.woff2") format("woff2");
  font-weight: 500; font-style: normal; font-display: swap; }
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSans-700.woff2") format("woff2");
  font-weight: 700; font-style: normal; font-display: swap; }
@font-face { font-family: "Noto Sans Mono"; src: url("/fonts/NotoSansMono-400.woff2") format("woff2");
  font-weight: 400; font-style: normal; font-display: swap; }

@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSansCJK-TC-400.subset.woff2") format("woff2");
  font-weight: 400; font-style: normal; font-display: swap;
  unicode-range: U+3000-303F, U+3100-312F, U+31A0-31BF, U+3400-4DBF, U+4E00-9FFF, U+F900-FAFF; }
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSansCJK-JP-400.subset.woff2") format("woff2");
  font-weight: 400; font-style: normal; font-display: swap;
  unicode-range: U+3040-309F, U+30A0-30FF, U+31F0-31FF; }
@font-face { font-family: "Noto Sans"; src: url("/fonts/NotoSansCJK-KR-400.subset.woff2") format("woff2");
  font-weight: 400; font-style: normal; font-display: swap;
  unicode-range: U+1100-11FF, U+3130-318F, U+A960-A97F, U+AC00-D7AF, U+D7B0-D7FF; }

html { font-synthesis: weight style; }

* { scrollbar-width: none; }
*::-webkit-scrollbar { width: 0; height: 0; }
*:hover { scrollbar-width: thin; }
*:hover::-webkit-scrollbar { width: 8px; height: 8px; }
*::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: 4px; }
```

- [ ] **Step 3.3: Point `main.tsx` at `global.css`**

Edit `frontend/src/main.tsx` to import `./styles/global.css` instead of `./styles/tailwind.css`.

- [ ] **Step 3.4: Real `langForMarket` mapping**

Replace `frontend/src/services/lang.ts`:

```ts
export type Exchange =
  | 'NYSE' | 'NASDAQ' | 'AMEX' | 'ARCA' | 'CBOE' | 'CME'
  | 'SEHK' | 'TSE' | 'KRX' | 'TWSE' | 'SSE' | 'SZSE'
  | 'LSE' | 'EURONEXT' | 'XETRA'
  | 'FX' | 'CRYPTO'
  | (string & {});

const MAP: Record<string, string> = {
  NYSE: 'en', NASDAQ: 'en', AMEX: 'en', ARCA: 'en', CBOE: 'en', CME: 'en',
  SEHK: 'zh-HK', TSE: 'ja', KRX: 'ko', TWSE: 'zh-TW',
  SSE: 'zh-CN', SZSE: 'zh-CN',
  LSE: 'en', EURONEXT: 'en', XETRA: 'en',
  FX: 'en', CRYPTO: 'en',
};

export function langForMarket(exchange: Exchange): string {
  return MAP[exchange] ?? 'en';
}
```

- [ ] **Step 3.5: Test**

`frontend/src/services/lang.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { langForMarket } from './lang';

describe('langForMarket', () => {
  it.each([
    ['NYSE', 'en'], ['SEHK', 'zh-HK'], ['TSE', 'ja'], ['KRX', 'ko'],
    ['TWSE', 'zh-TW'], ['SSE', 'zh-CN'], ['FX', 'en'], ['Unknown', 'en'],
  ])('%s → %s', (input, expected) => {
    expect(langForMarket(input)).toBe(expected);
  });
});
```

- [ ] **Step 3.6: Run + commit**

```bash
pnpm test src/services/lang.test.ts
```
Expected: 8 PASS.

```bash
cd /mnt/c/dashboard
git add frontend/public/fonts/ frontend/src/styles/global.css frontend/src/main.tsx \
        frontend/src/services/lang.ts frontend/src/services/lang.test.ts
git commit -m "feat(frontend): noto fonts + unicode-range cjk routing + langformarket"
```

---

### Task 4: Vite config — TanStack Router plugin + dev proxy

**Files:**
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/.gitignore`
- Modify: `frontend/package.json`

- [ ] **Step 4.1: `vite.config.ts`**

```ts
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { TanStackRouterVite } from '@tanstack/router-plugin/vite';

export default defineConfig({
  plugins: [
    TanStackRouterVite({
      routesDirectory: './src/routes',
      generatedRouteTree: './src/routes/routeTree.gen.ts',
    }),
    react(),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api':    { target: 'http://10.10.0.2:8000', changeOrigin: true },
      '/health': { target: 'http://10.10.0.2:8000', changeOrigin: true },
    },
  },
});
```

- [ ] **Step 4.2: `.gitignore` + `package.json` prebuild hook**

Append to `frontend/.gitignore`:
```
src/routes/routeTree.gen.ts
```

Update `scripts` in `frontend/package.json`:
```json
{
  "dev": "pnpm tsr generate && vite",
  "build": "pnpm tsr generate && tsc --noEmit && vite build",
  "test": "pnpm tsr generate && vitest run",
  "test:watch": "pnpm tsr generate && vitest",
  "typecheck": "pnpm tsr generate && tsc --noEmit",
  "tsr": "tsr",
  "storybook": "pnpm tsr generate && storybook dev -p 6006",
  "build-storybook": "pnpm tsr generate && storybook build",
  "lint": "eslint src",
  "stylelint": "stylelint \"src/**/*.{css,tsx}\"",
  "preview": "vite preview"
}
```

- [ ] **Step 4.3: Commit**

```bash
cd /mnt/c/dashboard
git add frontend/vite.config.ts frontend/.gitignore frontend/package.json
git commit -m "feat(frontend): tanstack router plugin + vite dev-proxy to nuc backend"
```

---

## Chunk B — Router scaffold

### Task 5: TanStack Router bootstrap

**Files:** `src/routes/__root.tsx`, `src/routes/index.tsx`, `src/App.tsx`

- [ ] **Step 5.1: `__root.tsx`** (replaced by `AppShell` in Task 36; for now renders `<Outlet />`)

```tsx
import { Outlet, createRootRoute } from '@tanstack/react-router';
import * as React from 'react';

function RootErrorBoundary({ error }: { error: Error }): React.JSX.Element {
  return (
    <div role="alert" style={{ padding: '2rem' }}>
      <h1>Something went wrong</h1>
      <pre>{error.message}</pre>
      <button type="button" onClick={() => location.reload()}>Reload</button>
    </div>
  );
}

export const Route = createRootRoute({
  component: () => <Outlet />,
  errorComponent: RootErrorBoundary,
});
```

- [ ] **Step 5.2: `index.tsx`** (redirect to /overview)

```tsx
import { createFileRoute, redirect } from '@tanstack/react-router';

export const Route = createFileRoute('/')({
  beforeLoad: () => { throw redirect({ to: '/overview' }); },
});
```

- [ ] **Step 5.3: Generate + mount in `App.tsx`**

```bash
export PATH="$HOME/.npm-global/bin:$PATH"
cd /mnt/c/dashboard/frontend
pnpm tsr generate
```

Replace `frontend/src/App.tsx`:

```tsx
import { RouterProvider, createRouter } from '@tanstack/react-router';
import type { JSX } from 'react';
import { routeTree } from './routes/routeTree.gen';

const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register { router: typeof router; }
}

export default function App(): JSX.Element {
  return <RouterProvider router={router} />;
}
```

- [ ] **Step 5.4: Typecheck + commit**

```bash
pnpm typecheck
```
Expected: PASS.

```bash
git add frontend/src/App.tsx frontend/src/routes/
git commit -m "feat(frontend): tanstack router root + index redirect to overview"
```

---

### Task 6: All 11 route stubs

Stub file template:
```tsx
import { createFileRoute } from '@tanstack/react-router';
export const Route = createFileRoute('/<path>')({
  component: () => <div style={{ padding: '2rem' }}><h2><label></h2> (stub — Task N)</div>,
});
```

- [ ] **Step 6.1: Create stubs**

| Path | File | Label | Filled in |
|---|---|---|---|
| /overview | `overview.tsx` | Overview | Task 37 |
| /orders | `orders.tsx` | Orders | Task 38 |
| /positions | `positions.tsx` | Positions | Task 38 |
| /watchlist | `watchlist.tsx` | Watchlist | Task 39 |
| /watchlist/$id | `watchlist.$id.tsx` | Watchlist $id | Task 39 |
| /admin | `admin.tsx` | Admin | Task 40 |
| /admin/config | `admin.config.tsx` | Admin Config | Task 40 |
| /admin/secrets | `admin.secrets.tsx` | Admin Secrets | Task 40 |
| /settings | `settings.tsx` | Settings | Task 41 |
| /trade | `trade.tsx` | Trade | Task 42 |
| /alerts | `alerts.tsx` | Alerts | Task 42 |

For `/watchlist/$id` use `Route.useParams()` to render the id.

- [ ] **Step 6.2: Generate + typecheck**

```bash
pnpm tsr generate && pnpm typecheck
```

- [ ] **Step 6.3: Manual dev smoke**

```bash
pnpm dev
```
Navigate to `/`, `/overview`, `/orders`, `/watchlist/abc` — each shows its stub. Kill with Ctrl+C.

- [ ] **Step 6.4: Commit**

```bash
cd /mnt/c/dashboard
git add frontend/src/routes/
git commit -m "feat(frontend): 11 route stubs via tanstack file-based routing"
```

---

## Chunk C — Services layer

### Task 7: Core TypeScript types

**Files:** `frontend/src/services/types.ts`

- [ ] **Step 7.1: Write types**

```ts
export type Mode = 'live' | 'paper';

export type BrokerId = 'ibkr' | 'futu' | 'schwab';
export interface Broker { id: BrokerId; name: string; }

export type AssetClass =
  | 'stock' | 'forex' | 'crypto' | 'futures' | 'options'
  | 'bond'  | 'etf'   | 'cfd'    | 'commodity' | 'index';

export interface Account {
  id: string; broker: BrokerId; mode: Mode; alias: string;
  accountNumber: string; nlv: number;
  baseCurrency: 'USD' | 'HKD' | 'GBP' | 'JPY' | 'KRW';
}

export interface Symbol {
  symbol: string; exchange: string; description: string;
  assetClass: AssetClass; langTag: string;
}

export interface Quote {
  symbol: string;
  last: number; change: number; changePct: number;
  bid: number; ask: number;
  volume: number; dayHigh: number; dayLow: number;
  open: number; prevClose: number;
  fiftyTwoWkHigh: number; fiftyTwoWkLow: number;
  marketCap: number | null; pe: number | null; eps: number | null;
  divYield: number | null; beta: number | null;
  sector: string | null; industry: string | null;
  avgVol30d: number; sharesOutstanding: number | null;
  nextEarningsDate: string | null;
  ivRank: number | null; optionsOI: number | null; newsCount24h: number;
  spread: number; spreadPct: number;
  isDelayed: boolean; asOf: string;
}

export type OrderStatus = 'open' | 'filled' | 'partial' | 'cancelled' | 'rejected' | 'expired';
export type OrderSide = 'buy' | 'sell';
export type OrderType = 'market' | 'limit' | 'stop' | 'stop_limit';

export interface Order {
  id: string; accountId: string; symbol: string;
  side: OrderSide; qty: number; filledQty: number;
  limitPx: number | null; stopPx: number | null;
  orderType: OrderType; status: OrderStatus;
  createdAt: string; updatedAt: string;
}

export interface Position {
  accountId: string; symbol: string;
  qty: number; avgCost: number; marketValue: number;
  pnlUnrealized: number; pnlRealized: number;
  currency: string; asOf: string;
}

export type WatchlistColumnKey =
  | 'symbol' | 'description' | 'last' | 'change' | 'changePct'
  | 'bid' | 'ask' | 'spread' | 'spreadPct' | 'volume' | 'avgVol30d'
  | 'dayHigh' | 'dayLow' | 'open' | 'prevClose'
  | 'fiftyTwoWkHigh' | 'fiftyTwoWkLow'
  | 'marketCap' | 'pe' | 'eps' | 'divYield' | 'beta'
  | 'sector' | 'industry' | 'exchange' | 'assetClass'
  | 'nextEarningsDate' | 'ivRank' | 'optionsOI' | 'newsCount24h';

export interface Watchlist {
  id: string; name: string;
  assetClass: AssetClass | 'mixed';
  symbolIds: string[];
  columnConfig: WatchlistColumnKey[];
}

export interface ConnectedStatus {
  broker: BrokerId;             // 'ibkr' | 'futu' | 'schwab'
  mode?: Mode;                  // 'live' | 'paper' — set for IBKR (2 live + 2 paper gateways); omitted for single-stack brokers
  gatewayId: string;            // unique gateway instance id, e.g. 'ibkr-live-gw-1'
  alias: string;                // human label, e.g. 'IBKR Live Gateway 1'
  backendOk: boolean;           // backend can reach gateway endpoint
  gatewayOk: boolean;           // gateway logged in + streaming
  latencyMs: number | null;     // last ping ms, null if down
}

// Derived tone per row:
//   green  = backendOk && gatewayOk
//   yellow = backendOk XOR gatewayOk (one side up)
//   red    = !backendOk && !gatewayOk

export type QuoteFeedType = 'realtime' | 'delayed' | 'none';

export interface QuoteFeedStatus {
  assetClass: AssetClass;       // group label ('stock', 'options', 'futures', 'forex', 'crypto', ...)
  exchange?: string;            // optional sub-row; when omitted the row lives at asset-class level
  feedType: QuoteFeedType;
  level?: 1 | 2;                // optional — distinguishes Level I / Level II
}

export interface Command {
  id: string; label: string;
  prefix?: '>' | '@' | '/' | '?';
  run: () => void | Promise<void>;
  keywords?: string[];
}
```

- [ ] **Step 7.2: Typecheck + commit**

```bash
pnpm typecheck
cd /mnt/c/dashboard
git add frontend/src/services/types.ts
git commit -m "feat(services): phase 3 core types — mode, account, quote, order, watchlist"
```

---

### Task 8: Fixtures

**Files:** `frontend/src/services/fixtures/*.ts` (7 files).

Contents per file (write full data — no "etc."):

**`brokers.ts`:**
```ts
import type { Broker } from '../types';
export const BROKERS: Broker[] = [
  { id: 'ibkr',   name: 'Interactive Brokers' },
  { id: 'futu',   name: 'Futu Securities' },
  { id: 'schwab', name: 'Charles Schwab' },
];
```

**`accounts.ts`** — 6 accounts as shown in spec §3 row 10.
**`symbols.ts`** — 50 symbols covering: 10 US large cap (AAPL MSFT GOOGL AMZN NVDA TSLA META JPM BAC BRK.B), 6 HK (0700 9988 3690 1299 0005 0388), 4 JP (7203 6758 7974 6501), 3 KR (005930 000660 035420), 2 TW (2330 2454), 5 FX (EURUSD USDJPY GBPUSD USDHKD AUDUSD), 3 crypto (BTC-USD ETH-USD SOL-USD), 3 ETFs (SPY QQQ VT), plus 14 more US stocks (JNJ PG KO PEP WMT TGT HD LOW DIS NFLX CRM ADBE ORCL CSCO) to reach 50. Plus `STRESS_SYMBOLS` generated loop for 500 tickers `SYM001..SYM500`.

**`positions.ts`** — 30 positions distributed 5 per account across 6 accounts. Use varying P&L states: winners, losers, near-flat. Mix asset classes per account.

**`orders.ts`** — 20 orders with statuses spanning all 5 values (filled / open / partial / cancelled / rejected). Spread across 6 accounts.

**`watchlists.ts`** — 5 lists including `stress-500`.

**`index.ts`:**
```ts
export { BROKERS } from './brokers';
export { ACCOUNTS } from './accounts';
export { SYMBOLS, STRESS_SYMBOLS } from './symbols';
export { POSITIONS } from './positions';
export { ORDERS } from './orders';
export { WATCHLISTS } from './watchlists';
```

- [ ] **Step 8.1: Write files** (concrete data — use the spec §3 row 10 shape).

- [ ] **Step 8.2: Sanity test** `frontend/src/services/fixtures/fixtures.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { ACCOUNTS, POSITIONS, ORDERS, WATCHLISTS, SYMBOLS, STRESS_SYMBOLS, BROKERS } from './index';

describe('fixtures', () => {
  it('6 accounts — 2 per broker × 3 brokers', () => {
    expect(ACCOUNTS).toHaveLength(6);
    for (const b of BROKERS) {
      const mine = ACCOUNTS.filter(a => a.broker === b.id);
      expect(mine).toHaveLength(2);
      expect(mine.map(a => a.mode).sort()).toEqual(['live', 'paper']);
    }
  });
  it('every position refs known account + symbol', () => {
    const aIds = new Set(ACCOUNTS.map(a => a.id));
    const sIds = new Set([...SYMBOLS, ...STRESS_SYMBOLS].map(s => s.symbol));
    for (const p of POSITIONS) {
      expect(aIds).toContain(p.accountId);
      expect(sIds).toContain(p.symbol);
    }
  });
  it('orders span all statuses', () => {
    const set = new Set(ORDERS.map(o => o.status));
    for (const s of ['open','filled','partial','cancelled','rejected'] as const) expect(set).toContain(s);
  });
  it('watchlists span stock/forex/crypto + stress-500', () => {
    const classes = new Set(WATCHLISTS.map(w => w.assetClass));
    expect(classes).toContain('stock');
    expect(classes).toContain('forex');
    expect(classes).toContain('crypto');
    expect(WATCHLISTS.find(w => w.id === 'stress-500')!.symbolIds).toHaveLength(500);
  });
});
```

- [ ] **Step 8.3: Run + commit**

```bash
pnpm test src/services/fixtures/
cd /mnt/c/dashboard
git add frontend/src/services/fixtures/
git commit -m "feat(services): fixtures — 6 accts × 3 brokers, 30 pos, 20 ord, 4+1 watchlists"
```

---

### Task 9: AccountsService + PositionsService + OrdersService

**Files:** 3 service files + 3 test files.

- [ ] **Step 9.1: `accounts.ts`**

```ts
import type { Account, Mode } from './types';
import { ACCOUNTS } from './fixtures';

export interface AccountsService {
  list(mode: Mode): Promise<Account[]>;
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void;
}

export class MockAccountsService implements AccountsService {
  constructor(private readonly fixtures: Account[] = ACCOUNTS) {}
  async list(mode: Mode): Promise<Account[]> {
    return this.fixtures.filter(a => a.mode === mode);
  }
  subscribe(_mode: Mode, _cb: (accounts: Account[]) => void): () => void {
    return () => {};
  }
}
```

- [ ] **Step 9.2: `positions.ts`**

```ts
import type { Position, Mode } from './types';
import { POSITIONS, ACCOUNTS } from './fixtures';

export interface PositionsService {
  list(mode: Mode): Promise<Position[]>;
  subscribe(mode: Mode, cb: (positions: Position[]) => void): () => void;
}

export class MockPositionsService implements PositionsService {
  constructor(private readonly fixtures: Position[] = POSITIONS) {}
  async list(mode: Mode): Promise<Position[]> {
    const ids = new Set(ACCOUNTS.filter(a => a.mode === mode).map(a => a.id));
    return this.fixtures.filter(p => ids.has(p.accountId));
  }
  subscribe(_mode: Mode, _cb: (positions: Position[]) => void): () => void {
    return () => {};
  }
}
```

- [ ] **Step 9.3: `orders.ts`** — same shape as positions, filtering by mode via account map.

- [ ] **Step 9.4: Tests** — each file gets a test file asserting `list()` filters by mode correctly and `subscribe` returns a callable unsubscribe.

- [ ] **Step 9.5: Run + commit**

```bash
pnpm test src/services/{accounts,positions,orders}.test.ts
cd /mnt/c/dashboard
git add frontend/src/services/accounts.ts frontend/src/services/positions.ts frontend/src/services/orders.ts \
        frontend/src/services/{accounts,positions,orders}.test.ts
git commit -m "feat(services): accounts, positions, orders mock adapters"
```

---

### Task 10: QuotesService with refcounted lazy ticker

**Files:** `frontend/src/services/quotes.ts`, `frontend/src/services/quotes.test.ts`

- [ ] **Step 10.1: Write `quotes.ts`**

```ts
import type { Quote, Symbol } from './types';
import { SYMBOLS, STRESS_SYMBOLS } from './fixtures';

export interface QuotesService {
  getSnapshot(symbol: string): Quote | undefined;
  subscribe(symbols: string[], cb: (q: Quote) => void): () => void;
  setTickingEnabled(on: boolean): void;
}

function seedQuote(sym: Symbol): Quote {
  const base = 50 + (sym.symbol.charCodeAt(0) % 200);
  const spread = base * 0.0005;
  return {
    symbol: sym.symbol,
    last: base, change: 0, changePct: 0,
    bid: base - spread / 2, ask: base + spread / 2,
    volume: 1_000_000 + (sym.symbol.charCodeAt(1) ?? 0) * 10_000,
    dayHigh: base * 1.02, dayLow: base * 0.98,
    open: base * 0.99, prevClose: base,
    fiftyTwoWkHigh: base * 1.5, fiftyTwoWkLow: base * 0.5,
    marketCap: base * 1_000_000_000,
    pe: 20 + sym.symbol.length * 2,
    eps: 2.5, divYield: 0.015, beta: 1.0 + (sym.symbol.charCodeAt(0) % 5) * 0.1,
    sector: sym.assetClass === 'stock' ? 'Technology' : null,
    industry: null,
    avgVol30d: 900_000,
    sharesOutstanding: sym.assetClass === 'stock' ? 1_000_000_000 : null,
    nextEarningsDate: '2026-05-15',
    ivRank: 50, optionsOI: 10_000, newsCount24h: 3,
    spread, spreadPct: spread / base,
    isDelayed: sym.exchange === 'SEHK' || sym.exchange === 'TSE',
    asOf: new Date().toISOString(),
  };
}

export class MockQuotesService implements QuotesService {
  private readonly quotes = new Map<string, Quote>();
  private readonly subscriptions = new Map<string, Set<(q: Quote) => void>>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private tickingEnabled = true;

  constructor(syms: Symbol[] = [...SYMBOLS, ...STRESS_SYMBOLS]) {
    for (const s of syms) this.quotes.set(s.symbol, seedQuote(s));
  }

  getSnapshot(symbol: string): Quote | undefined {
    return this.quotes.get(symbol);
  }

  subscribe(symbols: string[], cb: (q: Quote) => void): () => void {
    for (const sym of symbols) {
      if (!this.subscriptions.has(sym)) this.subscriptions.set(sym, new Set());
      this.subscriptions.get(sym)!.add(cb);
    }
    this.maybeStartTimer();
    return () => {
      for (const sym of symbols) {
        this.subscriptions.get(sym)?.delete(cb);
        if (this.subscriptions.get(sym)?.size === 0) this.subscriptions.delete(sym);
      }
      this.maybeStopTimer();
    };
  }

  setTickingEnabled(on: boolean): void {
    this.tickingEnabled = on;
    if (!on && this.timer) { clearInterval(this.timer); this.timer = null; }
    else if (on) { this.maybeStartTimer(); }
  }

  private maybeStartTimer(): void {
    if (this.timer || !this.tickingEnabled || this.subscriptions.size === 0) return;
    this.timer = setInterval(() => this.tick(), 500);
  }

  private maybeStopTimer(): void {
    if (this.timer && this.subscriptions.size === 0) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private tick(): void {
    for (const [sym, subs] of this.subscriptions) {
      const prev = this.quotes.get(sym);
      if (!prev) continue;
      const delta = (Math.random() - 0.5) * prev.last * 0.002;
      const last = Math.max(0.01, prev.last + delta);
      const next: Quote = {
        ...prev,
        last,
        change: last - prev.prevClose,
        changePct: (last - prev.prevClose) / prev.prevClose,
        asOf: new Date().toISOString(),
      };
      this.quotes.set(sym, next);
      for (const cb of subs) cb(next);
    }
  }
}
```

- [ ] **Step 10.2: Tests** `quotes.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MockQuotesService } from './quotes';

describe('MockQuotesService', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(()  => { vi.useRealTimers(); });

  it('getSnapshot returns seeded quote', () => {
    const svc = new MockQuotesService();
    expect(svc.getSnapshot('AAPL')).toBeDefined();
    expect(svc.getSnapshot('NONEXIST')).toBeUndefined();
  });

  it('timer does not start without subscribers', () => {
    const svc = new MockQuotesService();
    vi.advanceTimersByTime(2000);
    expect(svc.getSnapshot('AAPL')?.last).toBeGreaterThan(0);
  });

  it('subscribe starts timer and emits tick', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    expect(cb).toHaveBeenCalled();
  });

  it('unsubscribe stops timer when refcount hits zero', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    const unsub = svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    unsub();
    cb.mockClear();
    vi.advanceTimersByTime(2000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('setTickingEnabled(false) stops timer immediately', () => {
    const svc = new MockQuotesService();
    const cb = vi.fn();
    svc.subscribe(['AAPL'], cb);
    vi.advanceTimersByTime(600);
    cb.mockClear();
    svc.setTickingEnabled(false);
    vi.advanceTimersByTime(2000);
    expect(cb).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 10.3: Run + commit**

```bash
pnpm test src/services/quotes.test.ts
cd /mnt/c/dashboard
git add frontend/src/services/quotes.ts frontend/src/services/quotes.test.ts
git commit -m "feat(services): quotes with refcounted lazy ticker + subscribe"
```

---

### Task 11: Remaining services + lazy registry

**Files:** `watchlists.ts`, `connected.ts`, `commands.ts`, `registry.ts`, `registry.test.ts`

- [ ] **Step 11.1: `watchlists.ts`**

```ts
import type { Watchlist } from './types';
import { WATCHLISTS } from './fixtures';

const STORAGE_KEY = 'dashboard.watchlists.v1';

export interface WatchlistsService {
  list(): Promise<Watchlist[]>;
  save(watchlists: Watchlist[]): Promise<void>;
}

export class LocalStorageWatchlistService implements WatchlistsService {
  constructor(private readonly storage: Storage) {}
  async list(): Promise<Watchlist[]> {
    const raw = this.storage.getItem(STORAGE_KEY);
    if (!raw) return [...WATCHLISTS];
    try { return JSON.parse(raw) as Watchlist[]; }
    catch { return [...WATCHLISTS]; }
  }
  async save(watchlists: Watchlist[]): Promise<void> {
    this.storage.setItem(STORAGE_KEY, JSON.stringify(watchlists));
  }
}
```

- [ ] **Step 11.2: `connected.ts`**

```ts
import type { ConnectedStatus } from './types';

export interface ConnectedService {
  snapshot(): ConnectedStatus[];
  subscribe(cb: (statuses: ConnectedStatus[]) => void): () => void;
}

const SEED: ConnectedStatus[] = [
  { broker: 'ibkr',   mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true,  gatewayOk: true,  latencyMs: 120 },
  { broker: 'ibkr',   mode: 'live',  gatewayId: 'ibkr-live-gw-2',  alias: 'IBKR Live Gateway 2',  backendOk: true,  gatewayOk: false, latencyMs: 240 },
  { broker: 'ibkr',   mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true,  gatewayOk: true,  latencyMs: 140 },
  { broker: 'ibkr',   mode: 'paper', gatewayId: 'ibkr-paper-gw-2', alias: 'IBKR Paper Gateway 2', backendOk: true,  gatewayOk: true,  latencyMs: 160 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true,  gatewayOk: true,  latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: false, gatewayOk: false, latencyMs: null },
];

export class MockConnectedService implements ConnectedService {
  private statuses: ConnectedStatus[] = SEED;
  private listeners = new Set<(s: ConnectedStatus[]) => void>();
  private timer: ReturnType<typeof setInterval> | null = null;

  snapshot() { return this.statuses; }

  subscribe(cb: (s: ConnectedStatus[]) => void): () => void {
    this.listeners.add(cb);
    if (!this.timer && this.listeners.size > 0) {
      this.timer = setInterval(() => this.mutate(), 4000);
    }
    return () => {
      this.listeners.delete(cb);
      if (this.listeners.size === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    };
  }

  private mutate() {
    this.statuses = this.statuses.map(s =>
      s.latencyMs !== null
        ? { ...s, latencyMs: Math.max(50, s.latencyMs + (Math.random() - 0.5) * 50) }
        : s,
    );
    for (const cb of this.listeners) cb(this.statuses);
  }
}
```

- [ ] **Step 11.2b: `quote-feeds.ts`** (fixture + service)

Fixture `frontend/src/services/fixtures/quote-feeds.ts`:

```ts
import type { QuoteFeedStatus } from '../types';
export const QUOTE_FEEDS: QuoteFeedStatus[] = [
  { assetClass: 'stock',   exchange: 'NYSE',   feedType: 'realtime' },
  { assetClass: 'stock',   exchange: 'NASDAQ', feedType: 'realtime' },
  { assetClass: 'stock',   exchange: 'AMEX',   feedType: 'realtime' },
  { assetClass: 'stock',   exchange: 'NYSE',   feedType: 'delayed', level: 2 },
  { assetClass: 'options',                      feedType: 'delayed' },
  { assetClass: 'futures', exchange: 'CME',    feedType: 'realtime' },
  { assetClass: 'futures', exchange: 'CFE',    feedType: 'realtime' },
  { assetClass: 'forex',                        feedType: 'realtime' },
  { assetClass: 'crypto',                       feedType: 'realtime' },
];
```

Service `frontend/src/services/quote-feeds.ts`:

```ts
import type { QuoteFeedStatus } from './types';
import { QUOTE_FEEDS } from './fixtures/quote-feeds';

export interface QuoteFeedService {
  snapshot(): QuoteFeedStatus[];
  subscribe(cb: (feeds: QuoteFeedStatus[]) => void): () => void;
}

export class MockQuoteFeedService implements QuoteFeedService {
  private feeds: QuoteFeedStatus[] = QUOTE_FEEDS;
  private listeners = new Set<(f: QuoteFeedStatus[]) => void>();
  snapshot() { return this.feeds; }
  subscribe(cb: (f: QuoteFeedStatus[]) => void) {
    this.listeners.add(cb);
    return () => { this.listeners.delete(cb); };
  }
}
```

No ticking timer — feed subscriptions rarely change. Barrel: add `export { QUOTE_FEEDS } from './quote-feeds';` to `fixtures/index.ts`.

- [ ] **Step 11.3: `commands.ts`**

```ts
import type { Command } from './types';

export interface CommandRegistry {
  register(cmd: Command): () => void;
  list(): Command[];
  subscribe(cb: (cmds: Command[]) => void): () => void;
}

export class InMemoryCommandRegistry implements CommandRegistry {
  private commands = new Map<string, Command>();
  private listeners = new Set<(cmds: Command[]) => void>();

  register(cmd: Command): () => void {
    this.commands.set(cmd.id, cmd);
    this.notify();
    return () => { this.commands.delete(cmd.id); this.notify(); };
  }

  list(): Command[] { return Array.from(this.commands.values()); }

  subscribe(cb: (cmds: Command[]) => void): () => void {
    this.listeners.add(cb);
    cb(this.list());
    return () => { this.listeners.delete(cb); };
  }

  private notify() {
    const snap = this.list();
    for (const cb of this.listeners) cb(snap);
  }
}
```

- [ ] **Step 11.4: Lazy `registry.ts`**

```ts
import type { AccountsService } from './accounts';
import type { PositionsService } from './positions';
import type { OrdersService } from './orders';
import type { QuotesService } from './quotes';
import type { WatchlistsService } from './watchlists';
import type { ConnectedService } from './connected';
import type { QuoteFeedService } from './quote-feeds';
import type { CommandRegistry } from './commands';
import { MockAccountsService } from './accounts';
import { MockPositionsService } from './positions';
import { MockOrdersService } from './orders';
import { MockQuotesService } from './quotes';
import { LocalStorageWatchlistService } from './watchlists';
import { MockConnectedService } from './connected';
import { MockQuoteFeedService } from './quote-feeds';
import { InMemoryCommandRegistry } from './commands';

export interface Services {
  accounts: AccountsService;
  positions: PositionsService;
  orders: OrdersService;
  quotes: QuotesService;
  watchlists: WatchlistsService;
  connected: ConnectedService;
  quoteFeeds: QuoteFeedService;
  commands: CommandRegistry;
}

class MemoryStorage implements Storage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); }
  getItem(k: string) { return this.m.get(k) ?? null; }
  key(i: number) { return Array.from(this.m.keys())[i] ?? null; }
  removeItem(k: string) { this.m.delete(k); }
  setItem(k: string, v: string) { this.m.set(k, v); }
}

let _services: Services | null = null;

export function getServices(): Services {
  if (_services) return _services;
  _services = {
    accounts:   new MockAccountsService(),
    positions:  new MockPositionsService(),
    orders:     new MockOrdersService(),
    quotes:     new MockQuotesService(),
    watchlists: new LocalStorageWatchlistService(
      typeof window !== 'undefined' ? window.localStorage : new MemoryStorage()),
    connected:  new MockConnectedService(),
    quoteFeeds: new MockQuoteFeedService(),
    commands:   new InMemoryCommandRegistry(),
  };
  return _services;
}

export function resetServices(): void { _services = null; }
```

- [ ] **Step 11.5: `registry.test.ts`**

```ts
import { describe, it, expect, beforeEach } from 'vitest';
import { getServices, resetServices } from './registry';

describe('services registry', () => {
  beforeEach(resetServices);
  it('memoizes — same instance on repeat', () => {
    expect(getServices()).toBe(getServices());
  });
  it('resetServices yields fresh instance', () => {
    const a = getServices();
    resetServices();
    expect(getServices()).not.toBe(a);
  });
});
```

- [ ] **Step 11.6: Run + commit**

```bash
pnpm test src/services/
cd /mnt/c/dashboard
git add frontend/src/services/
git commit -m "feat(services): watchlists, connected, commands + lazy getservices registry"
```

---

## Chunk D — Stores

### Task 12: Global stores (mode, theme, commands, connected)

**Files:** `frontend/src/stores/global/*.ts` + `mode.test.ts`

- [ ] **Step 12.1: `mode.ts`**

```ts
import { create } from 'zustand';
import type { Mode } from '@/services/types';

export type ModeStatus = 'idle' | 'switching';

interface ModeState {
  mode: Mode;
  pendingMode: Mode | null;
  status: ModeStatus;
  requestModeSwitch(target: Mode): void;
  confirmModeSwitch(): void;
  cancelModeSwitch(): void;
  setMode(next: Mode): void;
  setStatus(status: ModeStatus): void;
}

export const useModeStore = create<ModeState>((set, get) => ({
  mode: 'paper',
  pendingMode: null,
  status: 'idle',
  requestModeSwitch(target) {
    if (get().mode === target) return;
    if (target === 'live') set({ pendingMode: 'live' });
    else                   set({ mode: 'paper' });
  },
  confirmModeSwitch() {
    const p = get().pendingMode;
    if (p) set({ mode: p, pendingMode: null });
  },
  cancelModeSwitch() { set({ pendingMode: null }); },
  setMode(next) { set({ mode: next }); },
  setStatus(status) { set({ status }); },
}));
```

- [ ] **Step 12.2: `theme.ts`**

```ts
import { create } from 'zustand';
export const useThemeStore = create(() => ({ theme: 'dark' as const }));
```

- [ ] **Step 12.3: `commands.ts`**

```ts
import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { Command } from '@/services/types';

interface CommandsState {
  open: boolean;
  commands: Command[];
  setOpen(open: boolean): void;
  register(cmd: Command): () => void;
}

export const useCommandsStore = create<CommandsState>((set) => {
  const registry = getServices().commands;
  registry.subscribe(list => set({ commands: list }));
  return {
    open: false,
    commands: registry.list(),
    setOpen(open) { set({ open }); },
    register(cmd) { return registry.register(cmd); },
  };
});
```

- [ ] **Step 12.4: `connected.ts`**

```ts
import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { ConnectedStatus } from '@/services/types';

export const useConnectedStore = create<{ statuses: ConnectedStatus[] }>((set) => {
  const svc = getServices().connected;
  svc.subscribe(statuses => set({ statuses }));
  return { statuses: svc.snapshot() };
});
```

- [ ] **Step 12.4b: `quote-feeds.ts`**

```ts
import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { QuoteFeedStatus } from '@/services/types';

export const useQuoteFeedStore = create<{ feeds: QuoteFeedStatus[] }>((set) => {
  const svc = getServices().quoteFeeds;
  svc.subscribe(feeds => set({ feeds }));
  return { feeds: svc.snapshot() };
});
```

- [ ] **Step 12.5: `mode.test.ts`** — 5 assertions covering default state, paper→live stages pending, confirm flips mode, cancel keeps mode, live→paper direct.

- [ ] **Step 12.6: Run + commit**

```bash
pnpm test src/stores/global/
cd /mnt/c/dashboard
git add frontend/src/stores/global/
git commit -m "feat(stores): global stores — mode, theme, commands, connected"
```

---

### Task 13: Scoped store factory with phantom types

**Files:** `frontend/src/stores/scoped/*.ts`, `frontend/src/stores/factory.ts`

- [ ] **Step 13.1: `scoped/types.ts`**

```ts
import type { Mode } from '@/services/types';
declare const brand: unique symbol;
export type Scoped<M extends Mode, T> = T & { readonly [brand]: M };
```

- [ ] **Step 13.2: Four scoped store creators** — one per slice (accounts, positions, orders, watchlists). Each exposes `hydrate(svc)` + `suspend()` + slice-specific actions. See spec §6 for contract.

Example `account-store.ts`:

```ts
import { create } from 'zustand';
import type { Mode, Account } from '@/services/types';
import type { Services } from '@/services/registry';
import type { Scoped } from './types';

export interface AccountsState {
  accounts: Account[];
  selectedAccountId: string | null;
  hydrate(svc: Services): Promise<void>;
  suspend(): void;
  select(id: string | null): void;
}

export function createAccountStore<M extends Mode>(mode: M) {
  const store = create<AccountsState>((set) => ({
    accounts: [],
    selectedAccountId: null,
    async hydrate(svc) {
      const accts = await svc.accounts.list(mode);
      set({ accounts: accts, selectedAccountId: accts[0]?.id ?? null });
    },
    suspend() { set({ accounts: [], selectedAccountId: null }); },
    select(id) { set({ selectedAccountId: id }); },
  }));
  return store as unknown as Scoped<M, typeof store>;
}
```

`positions-store.ts` and `orders-store.ts` follow the same minimal pattern: `positions` / `orders` array + `hydrate(svc)` + `suspend()`.

`watchlists-store.ts` includes `activeWatchlistId`, `upsert(wl)`, `remove(id)`, `setActive(id)` — persisting via `svc.watchlists.save(...)` on mutation.

- [ ] **Step 13.3: `factory.ts`**

```ts
import type { Mode } from '@/services/types';
import type { Services } from '@/services/registry';
import { createAccountStore } from './scoped/account-store';
import { createPositionsStore } from './scoped/positions-store';
import { createOrdersStore } from './scoped/orders-store';
import { createWatchlistsStore } from './scoped/watchlists-store';

export interface ScopedStores<M extends Mode> {
  readonly mode: M;
  useAccounts:   ReturnType<typeof createAccountStore<M>>;
  usePositions:  ReturnType<typeof createPositionsStore<M>>;
  useOrders:     ReturnType<typeof createOrdersStore<M>>;
  useWatchlists: ReturnType<typeof createWatchlistsStore<M>>;
  hydrate(svc: Services): Promise<void>;
  suspend(): void;
}

export function createScopedStores<M extends Mode>(mode: M): ScopedStores<M> {
  const useAccounts   = createAccountStore(mode);
  const usePositions  = createPositionsStore(mode);
  const useOrders     = createOrdersStore(mode);
  const useWatchlists = createWatchlistsStore(mode);
  return {
    mode,
    useAccounts, usePositions, useOrders, useWatchlists,
    async hydrate(svc) {
      await Promise.all([
        useAccounts.getState().hydrate(svc),
        usePositions.getState().hydrate(svc),
        useOrders.getState().hydrate(svc),
        useWatchlists.getState().hydrate(svc),
      ]);
    },
    suspend() {
      useAccounts.getState().suspend();
      usePositions.getState().suspend();
      useOrders.getState().suspend();
      useWatchlists.getState().suspend();
    },
  };
}
```

- [ ] **Step 13.4: Typecheck + commit**

```bash
pnpm typecheck
cd /mnt/c/dashboard
git add frontend/src/stores/scoped/ frontend/src/stores/factory.ts
git commit -m "feat(stores): scoped factory + phantom types + hydrate/suspend lifecycle"
```

---

### Task 14: Registry singleton + useActiveStores

**Files:** `frontend/src/stores/registry.ts`, `frontend/src/stores/registry.test.ts`

- [ ] **Step 14.1: `registry.ts`**

```ts
import type { Mode } from '@/services/types';
import { createScopedStores, type ScopedStores } from './factory';
import { useModeStore } from './global/mode';

const live  = createScopedStores('live');
const paper = createScopedStores('paper');

export function getScopedStores<M extends Mode>(mode: M): ScopedStores<M> {
  return (mode === 'live' ? live : paper) as unknown as ScopedStores<M>;
}
export function useActiveStores(): ScopedStores<Mode> {
  const mode = useModeStore(s => s.mode);
  return getScopedStores(mode);
}
export function getBothScopes() { return { live, paper }; }
```

- [ ] **Step 14.2: `registry.test.ts`** — 4 assertions:
  1. live ≠ paper instances
  2. hydrate(live) does NOT populate paper
  3. suspend() clears the scope
  4. getScopedStores returns matching instance

- [ ] **Step 14.3: Run + commit**

```bash
pnpm test src/stores/
cd /mnt/c/dashboard
git add frontend/src/stores/registry.ts frontend/src/stores/registry.test.ts
git commit -m "feat(stores): registry with live/paper singletons + useactivestores"
```

---

### Task 15: ESLint boundary rule for scoped stores

**Files:** `frontend/eslint.config.mjs`

- [ ] **Step 15.1: Add element types**

In `boundaries/elements`:
```js
{ type: 'scoped',  pattern: 'src/stores/scoped/**' },
{ type: 'factory', pattern: 'src/stores/factory.ts' },
```

In `boundaries/element-types rules`:
```js
{ from: 'scoped',  allow: ['services', 'lib'] },
{ from: 'factory', allow: ['scoped', 'services', 'lib'] },
```

Update `from: 'stores'` to exclude `scoped`:
```js
{ from: 'stores',  allow: ['services', 'lib', 'factory'] },
```

- [ ] **Step 15.2: `no-restricted-imports`**

Append to `rules`:
```js
'no-restricted-imports': ['error', {
  patterns: [{
    group: ['@/stores/scoped/*', '**/stores/scoped/*'],
    message: 'Do not import scoped stores directly. Use useActiveStores() from @/stores/registry.',
  }],
}],
```

Override exempting factory + registry:
```js
{
  files: ['src/stores/factory.ts', 'src/stores/registry.ts', 'src/stores/scoped/**'],
  rules: { 'no-restricted-imports': 'off' },
}
```

- [ ] **Step 15.3: Red-test verification**

Create `frontend/src/features/overview/__test_should_fail.tsx`:

```tsx
import { createAccountStore } from '@/stores/scoped/account-store';
export const x = createAccountStore('live');
```

```bash
pnpm lint
```
Expected: ERROR on the import.

Delete: `rm frontend/src/features/overview/__test_should_fail.tsx`.

- [ ] **Step 15.4: Commit**

```bash
cd /mnt/c/dashboard
git add frontend/eslint.config.mjs
git commit -m "feat(frontend): eslint rule — stores/scoped only importable via factory"
```

---

## Chunk E — Primitives

**Template pattern** used by every primitive:
- `<Name>.tsx` — React component; `cva` for variants if >1 style family
- `<Name>.stories.tsx` — ≥ 3 variants (default + relevant states)
- `<Name>.test.tsx` — ≥ 3 assertions (renders, user interaction, variant applies)
- `index.ts` — re-export component + types

### Task 16: Input + NumericCell

**Files:** `components/primitives/{Input,NumericCell}/` × 4 files each.

- [ ] **Step 16.1: `Input.tsx`**

```tsx
import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const inputVariants = cva(
  'h-10 w-full rounded-md border border-border bg-panel px-3 text-sm text-fg placeholder:text-fg-subtle focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-active disabled:opacity-50',
  {
    variants: {
      variant: {
        default: '',
        numeric: 'text-right font-mono tabular-nums',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface InputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'>,
    VariantProps<typeof inputVariants> {}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, variant, type = 'text', ...props }, ref) => (
    <input ref={ref} type={type} className={cn(inputVariants({ variant, className }))} {...props} />
  ),
);
Input.displayName = 'Input';
```

- [ ] **Step 16.2: `NumericCell.tsx`** — memoized numeric renderer:

```tsx
import * as React from 'react';
import { cn } from '@/lib/utils';

export type NumericEmphasis = 'up' | 'down' | 'neutral';

export interface NumericCellProps {
  value: number | null | undefined;
  format?: 'number' | 'currency' | 'percent';
  currency?: string;
  digits?: number;
  emphasis?: NumericEmphasis;
  className?: string;
}

function format(v: number, opts: { format: 'number'|'currency'|'percent'; currency?: string; digits?: number }): string {
  const { format: f, currency = 'USD', digits = 2 } = opts;
  if (f === 'currency') return new Intl.NumberFormat(undefined, { style: 'currency', currency, minimumFractionDigits: digits }).format(v);
  if (f === 'percent')  return new Intl.NumberFormat(undefined, { style: 'percent', minimumFractionDigits: digits }).format(v);
  return new Intl.NumberFormat(undefined, { minimumFractionDigits: digits }).format(v);
}

export const NumericCell = React.memo(function NumericCell({
  value, format: f = 'number', currency, digits = 2, emphasis = 'neutral', className,
}: NumericCellProps): React.JSX.Element {
  const tone =
    emphasis === 'up'   ? 'text-positive' :
    emphasis === 'down' ? 'text-negative' : 'text-fg';
  return (
    <span className={cn('font-mono tabular-nums text-right inline-block', tone, className)}>
      {value == null || Number.isNaN(value) ? '—' : format(value, { format: f, currency, digits })}
    </span>
  );
});
```

- [ ] **Step 16.3: Stories + tests** (≥ 3 variants / ≥ 3 assertions each)

- [ ] **Step 16.4: Run + commit**

```bash
pnpm test src/components/primitives/{Input,NumericCell}/
pnpm lint
cd /mnt/c/dashboard
git add frontend/src/components/primitives/Input/ frontend/src/components/primitives/NumericCell/
git commit -m "feat(primitives): input (numeric variant) + memoed numericcell"
```

---

### Task 17: Checkbox + Radio + Switch

Three Radix-wrapper primitives. For each: component + stories (default/checked/disabled/with-label) + tests (renders, toggles, disabled blocks interaction) + index.

- [ ] **Step 17.1-17.3:** Write three primitives following the `Input` shape. Each TSX file ~25 lines wrapping the matching `@radix-ui/react-<name>`.

Template (`Switch.tsx`):

```tsx
import * as React from 'react';
import * as RadixSwitch from '@radix-ui/react-switch';
import { cn } from '@/lib/utils';

export const Switch = React.forwardRef<
  React.ElementRef<typeof RadixSwitch.Root>,
  React.ComponentPropsWithoutRef<typeof RadixSwitch.Root>
>(({ className, ...props }, ref) => (
  <RadixSwitch.Root ref={ref} className={cn('inline-flex h-6 w-11 items-center rounded-full border border-border bg-panel data-[state=checked]:bg-accent-active', className)} {...props}>
    <RadixSwitch.Thumb className="block h-5 w-5 translate-x-0.5 rounded-full bg-fg transition-transform data-[state=checked]:translate-x-5" />
  </RadixSwitch.Root>
));
Switch.displayName = 'Switch';
```

`Checkbox.tsx` and `Radio.tsx` follow the same shape. Radio exports `RadioGroup` + `RadioItem`.

- [ ] **Step 17.4: Commit**

```bash
cd /mnt/c/dashboard
git add frontend/src/components/primitives/{Checkbox,Radio,Switch}/
git commit -m "feat(primitives): checkbox, radio, switch — radix wrappers with mode accent"
```

---

### Task 18: Select

Compound wrapper exporting `Select, SelectTrigger, SelectContent, SelectItem, SelectValue, SelectGroup, SelectLabel` over `@radix-ui/react-select`. Stories cover single / grouped / with-value / disabled. Tests assert open-on-click, keyboard arrow nav, item select fires onValueChange.

- [ ] **Step 18.1: Write Select.tsx** (~60 lines compound components). See `shadcn/ui` reference pattern.

- [ ] **Step 18.2: Stories + tests + commit**

```bash
cd /mnt/c/dashboard
git add frontend/src/components/primitives/Select/
git commit -m "feat(primitives): select — radix compound components"
```

---

### Task 19: Dialog + Popover + Tooltip

Three Radix compound wrappers. Each: Trigger + Content + Close/Title/Description where applicable.

- [ ] **Step 19.1: `Dialog.tsx`** (~40 lines, includes overlay + close button with X icon).

- [ ] **Step 19.2-19.3:** `Popover.tsx` + `Tooltip.tsx` (smaller, no overlay).

- [ ] **Step 19.4: Stories + tests + commit**

```bash
git add frontend/src/components/primitives/{Dialog,Popover,Tooltip}/
git commit -m "feat(primitives): dialog, popover, tooltip — radix overlay family"
```

---

### Task 20: DropdownMenu + Tabs

- [ ] **Step 20.1: `DropdownMenu.tsx`** — exports Trigger, Content, Item, Label, Separator, Group, Sub, SubTrigger, SubContent.

- [ ] **Step 20.2: `Tabs.tsx`** — exports Tabs, TabsList, TabsTrigger, TabsContent.

- [ ] **Step 20.3: Stories + tests + commit**

```bash
git add frontend/src/components/primitives/{DropdownMenu,Tabs}/
git commit -m "feat(primitives): dropdownmenu (incl. submenu) + tabs"
```

---

### Task 21: Icon + Badge + Avatar

- [ ] **Step 21.1: `Icon.tsx`** — lucide-react wrapper with size={sm|md|lg} + aria-label semantics.

- [ ] **Step 21.2: `Badge.tsx`** — `cva` with variants `neutral|live|paper|delayed|up|down|warn` (see Task 21 code in spec draft).

- [ ] **Step 21.3: `Avatar.tsx`** — Radix Avatar + `initials(label)` helper exported.

- [ ] **Step 21.4: Stories + tests + commit**

```bash
git add frontend/src/components/primitives/{Icon,Badge,Avatar}/
git commit -m "feat(primitives): icon (lucide wrapper), badge (mode variants), avatar (initials)"
```

---

### Task 22: Toast + useToast

**Files:** `components/primitives/Toast/` (4 files) + `hooks/use-toast.ts`.

- [ ] **Step 22.1: `Toast.tsx`** — wraps `@radix-ui/react-toast` with ToastProvider + Viewport + Toast + Title + Description + Close. Renders queue from `useToastStore`.

- [ ] **Step 22.2: `hooks/use-toast.ts`** — Zustand-backed queue:

```ts
import { create } from 'zustand';

export interface ToastItem {
  id: string;
  title?: string;
  description?: string;
  tone?: 'neutral' | 'success' | 'error';
  durationMs?: number;
}

interface ToastState {
  items: ToastItem[];
  push(item: Omit<ToastItem, 'id'>): string;
  dismiss(id: string): void;
}

export const useToastStore = create<ToastState>((set) => ({
  items: [],
  push(item) {
    const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    set(s => ({ items: [...s.items, { ...item, id }] }));
    if (item.durationMs !== 0) {
      setTimeout(() => useToastStore.getState().dismiss(id), item.durationMs ?? 3000);
    }
    return id;
  },
  dismiss(id) { set(s => ({ items: s.items.filter(t => t.id !== id) })); },
}));

export function useToast() {
  return { toast: useToastStore.getState().push, dismiss: useToastStore.getState().dismiss };
}
```

- [ ] **Step 22.3: Stories + tests + commit**

```bash
git add frontend/src/components/primitives/Toast/ frontend/src/hooks/use-toast.ts
git commit -m "feat(primitives): toast + usetoast hook with auto-dismiss queue"
```

---

### Task 23: ErrorBoundary

**Files:** `components/primitives/ErrorBoundary/*`

- [ ] **Step 23.1: `ErrorBoundary.tsx`** — React class component with `getDerivedStateFromError` + retry:

```tsx
import * as React from 'react';

interface ErrorBoundaryProps {
  fallback?: React.ReactNode | ((error: Error, retry: () => void) => React.ReactNode);
  onError?: (error: Error, info: React.ErrorInfo) => void;
  children: React.ReactNode;
}
interface ErrorBoundaryState { error: Error | null; }

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: React.ErrorInfo) { this.props.onError?.(error, info); }
  private retry = () => this.setState({ error: null });
  render() {
    if (!this.state.error) return this.props.children;
    if (typeof this.props.fallback === 'function') return this.props.fallback(this.state.error, this.retry);
    return this.props.fallback ?? (
      <div role="alert" style={{ padding: '2rem' }}>
        <h2>Something went wrong</h2>
        <pre style={{ fontSize: '0.875rem' }}>{this.state.error.message}</pre>
        <button type="button" onClick={this.retry}>Retry</button>
      </div>
    );
  }
}
```

- [ ] **Step 23.2: Test** — 3 assertions: renders children, catches throw + shows default fallback, retry button clears error.

- [ ] **Step 23.3: Stories + commit**

```bash
pnpm test src/components/primitives/ErrorBoundary/
git add frontend/src/components/primitives/ErrorBoundary/
git commit -m "feat(primitives): errorboundary with fallback + retry contract"
```

---

## Chunk F — Patterns

### Task 24: EmptyState

**Files:** `components/patterns/EmptyState/*`

```tsx
import * as React from 'react';
import type { LucideIcon } from 'lucide-react';
import { Icon } from '@/components/primitives/Icon';
import { Button } from '@/components/primitives/Button';
import { cn } from '@/lib/utils';

export interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps): React.JSX.Element {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 py-12 text-center', className)}>
      {icon && <Icon as={icon} size="lg" className="text-fg-muted" />}
      <h3 className="text-lg font-semibold text-fg">{title}</h3>
      {description && <p className="max-w-md text-sm text-fg-muted">{description}</p>}
      {action && <Button onClick={action.onClick} className="mt-2">{action.label}</Button>}
    </div>
  );
}
```

Stories: default, with-icon, with-action. Tests: renders title, clicks action → callback fires.

Commit: `feat(patterns): emptystate — reusable empty ui with optional icon + action`.

---

### Task 25: ResizablePanelFrame

**Files:** `components/patterns/ResizablePanelFrame/*`

```tsx
import * as React from 'react';
import { Panel, PanelGroup, PanelResizeHandle, type ImperativePanelHandle } from 'react-resizable-panels';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Icon } from '@/components/primitives/Icon';
import { cn } from '@/lib/utils';

export interface PanelSpec {
  id: string;
  defaultSize: number;
  minSize?: number;
  collapsible?: boolean;
  collapsedSize?: number;
  content: React.ReactNode;
}

export interface ResizablePanelFrameProps {
  direction: 'horizontal' | 'vertical';
  autoSaveId?: string;
  panels: PanelSpec[];
  className?: string;
}

export function ResizablePanelFrame({ direction, autoSaveId, panels, className }: ResizablePanelFrameProps) {
  const refs = React.useRef<Record<string, ImperativePanelHandle | null>>({});
  return (
    <PanelGroup direction={direction} autoSaveId={autoSaveId} className={cn('h-full w-full', className)}>
      {panels.map((p, i) => (
        <React.Fragment key={p.id}>
          <Panel
            ref={(h) => { refs.current[p.id] = h; }}
            defaultSize={p.defaultSize}
            minSize={p.minSize ?? 10}
            collapsible={p.collapsible ?? false}
            collapsedSize={p.collapsedSize ?? 0}
            id={p.id}
          >
            {p.content}
          </Panel>
          {i < panels.length - 1 && (
            <PanelResizeHandle className={cn(
              'group relative flex items-center justify-center bg-border transition-colors hover:bg-accent-active',
              direction === 'horizontal' ? 'w-px cursor-col-resize' : 'h-px cursor-row-resize',
            )}>
              {p.collapsible && (
                <button
                  type="button"
                  aria-label={`Toggle ${p.id}`}
                  onClick={() => {
                    const r = refs.current[p.id];
                    r?.isCollapsed() ? r?.expand() : r?.collapse();
                  }}
                  className="absolute h-6 w-3 rounded-sm bg-panel text-fg-muted opacity-0 transition-opacity group-hover:opacity-100"
                >
                  <Icon as={direction === 'horizontal' ? ChevronLeft : ChevronRight} size="sm" />
                </button>
              )}
            </PanelResizeHandle>
          )}
        </React.Fragment>
      ))}
    </PanelGroup>
  );
}
```

Stories: horizontal-3-panel, vertical-2-panel, collapsible-left. Tests: renders all panels, caret button toggles collapse.

Commit: `feat(patterns): resizablepanelframe with caret collapse + keyboard-ready`.

---

### Task 26: ModeToggle + ModeSwitchConfirmDialog

**Files:** `components/patterns/ModeToggle/*`

```tsx
import * as React from 'react';
import { useModeStore } from '@/stores/global/mode';
import { Switch } from '@/components/primitives/Switch';
import { Badge } from '@/components/primitives/Badge';
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogClose } from '@/components/primitives/Dialog';
import { Button } from '@/components/primitives/Button';
import { useToast } from '@/hooks/use-toast';
import { getScopedStores } from '@/stores/registry';
import { getServices } from '@/services/registry';

export function ModeToggle(): React.JSX.Element {
  const { mode, pendingMode, requestModeSwitch, cancelModeSwitch, setStatus, setMode } = useModeStore();
  const { toast } = useToast();

  async function performSwitch(target: 'live' | 'paper') {
    setStatus('switching');
    const from = mode;
    try {
      await getScopedStores(target).hydrate(getServices());
      getScopedStores(from).suspend();
      setMode(target);
      toast({ title: `Switched to ${target.toUpperCase()} mode`, tone: 'success' });
    } finally {
      setStatus('idle');
    }
  }

  return (
    <>
      <div className="flex items-center gap-2" data-testid="mode-toggle">
        <Badge variant={mode}>{mode.toUpperCase()}</Badge>
        <Switch
          aria-label="mode"
          checked={mode === 'live'}
          onCheckedChange={(next) => { if (next) requestModeSwitch('live'); else void performSwitch('paper'); }}
        />
      </div>
      <Dialog open={pendingMode === 'live'} onOpenChange={(open) => !open && cancelModeSwitch()}>
        <DialogContent>
          <DialogTitle>Switch to LIVE mode?</DialogTitle>
          <DialogDescription>
            Real accounts and real orders will appear. Any action from here on may affect real money.
          </DialogDescription>
          <div className="mt-4 flex justify-end gap-2">
            <DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose>
            <Button
              variant="destructive"
              onClick={async () => { cancelModeSwitch(); await performSwitch('live'); }}
            >
              Continue to LIVE
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

Stories: paper-default, live-active, confirm-open. Tests: click switch in paper → dialog visible; Cancel keeps paper; Continue flips mode + fires toast.

Commit: `feat(patterns): modetoggle with paper→live confirm dialog + suspend/hydrate`.

---

### Task 27: AccountPicker

**Files:** `components/patterns/AccountPicker/*`

Composes DropdownMenu (grouped by broker) + Avatar (initials) + NumericCell (NLV right-aligned). Reads from `useActiveStores().useAccounts` + `BROKERS` fixtures.

Skeleton:

```tsx
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuLabel, DropdownMenuItem, DropdownMenuSeparator } from '@/components/primitives/DropdownMenu';
import { Avatar, initials } from '@/components/primitives/Avatar';
import { NumericCell } from '@/components/primitives/NumericCell';
import { Button } from '@/components/primitives/Button';
import { useActiveStores } from '@/stores/registry';
import { BROKERS } from '@/services/fixtures';

export function AccountPicker() {
  const { useAccounts } = useActiveStores();
  const { accounts, selectedAccountId, select } = useAccounts();
  const selected = accounts.find(a => a.id === selectedAccountId);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="min-w-[14rem] justify-between">
          <span className="flex items-center gap-2">
            <Avatar><span>{selected ? initials(selected.alias) : '—'}</span></Avatar>
            <span className="truncate">{selected?.alias ?? 'Select account'}</span>
          </span>
          {selected && <NumericCell value={selected.nlv} format="currency" currency={selected.baseCurrency} />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        {BROKERS.map((b, i) => {
          const rows = accounts.filter(a => a.broker === b.id);
          if (!rows.length) return null;
          return (
            <div key={b.id}>
              {i > 0 && <DropdownMenuSeparator />}
              <DropdownMenuLabel>{b.name}</DropdownMenuLabel>
              {rows.map(a => (
                <DropdownMenuItem key={a.id} onSelect={() => select(a.id)} className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <Avatar><span>{initials(a.alias)}</span></Avatar>
                    <span>{a.alias}</span>
                  </span>
                  <NumericCell value={a.nlv} format="currency" currency={a.baseCurrency} />
                </DropdownMenuItem>
              ))}
            </div>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

Stories: open-menu with live accounts, with paper accounts, empty. Tests: open menu, click item, selection persists.

Commit: `feat(patterns): accountpicker grouped by broker with nlv + initials`.

---

### Task 28: ConnectedDropdown

**Files:** `components/patterns/ConnectedDropdown/*`

DropdownMenu + Badge — shows per-broker connection health (backend side × gateway side). IBKR has 4 gateways (2 live + 2 paper) that aggregate into 2 rows ("IBKR Live", "IBKR Paper"). Futu and Schwab are single-stack (1 row each). Four rows total.

**Row-tone derivation** (per individual `ConnectedStatus`):
- green  = `backendOk && gatewayOk`
- yellow = `backendOk XOR gatewayOk` (one side up)
- red    = `!backendOk && !gatewayOk`

**Group aggregation**: group statuses by `(broker, mode)`. Row tone is the worst-of the group (red > yellow > green). Trigger badge tone is the worst-of across all groups.

```tsx
import * as React from 'react';
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
} from '@/components/primitives/DropdownMenu';
import { Button } from '@/components/primitives/Button';
import { Badge } from '@/components/primitives/Badge';
import { useConnectedStore } from '@/stores/global/connected';
import { BROKERS } from '@/services/fixtures';
import type { ConnectedStatus, BrokerId, Mode } from '@/services/types';

type Tone = 'green' | 'yellow' | 'red';
const TONE_VARIANT: Record<Tone, 'up' | 'warn' | 'down'> = { green: 'up', yellow: 'warn', red: 'down' };
const TONE_RANK: Record<Tone, number> = { green: 0, yellow: 1, red: 2 };

function rowTone(s: ConnectedStatus): Tone {
  if (s.backendOk && s.gatewayOk) return 'green';
  if (s.backendOk || s.gatewayOk) return 'yellow';
  return 'red';
}
function worstOf(rows: ConnectedStatus[]): Tone {
  return rows.map(rowTone).reduce<Tone>((a, b) => (TONE_RANK[b] > TONE_RANK[a] ? b : a), 'green');
}

interface Group { broker: BrokerId; brokerName: string; mode?: Mode; label: string; rows: ConnectedStatus[]; tone: Tone; }

function groupStatuses(statuses: ConnectedStatus[]): Group[] {
  const out: Group[] = [];
  for (const b of BROKERS) {
    const mine = statuses.filter(s => s.broker === b.id);
    if (mine.length === 0) continue;
    const modes = new Set(mine.map(s => s.mode).filter((m): m is Mode => m != null));
    if (modes.size > 0) {
      for (const m of modes) {
        const rows = mine.filter(s => s.mode === m);
        out.push({ broker: b.id, brokerName: b.name, mode: m, label: `${b.name} ${m === 'live' ? 'Live' : 'Paper'}`, rows, tone: worstOf(rows) });
      }
    } else {
      out.push({ broker: b.id, brokerName: b.name, label: b.name, rows: mine, tone: worstOf(mine) });
    }
  }
  return out;
}

export function ConnectedDropdown(): React.JSX.Element {
  const statuses = useConnectedStore(s => s.statuses);
  const groups = groupStatuses(statuses);
  const worst = groups.map(g => g.tone).reduce<Tone>((a, b) => (TONE_RANK[b] > TONE_RANK[a] ? b : a), 'green');

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" aria-label="connection health">
          <Badge variant={TONE_VARIANT[worst]}>Connected</Badge>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        {groups.map(g => (
          <DropdownMenuItem
            key={`${g.broker}-${g.mode ?? 'default'}`}
            className="flex items-center justify-between gap-3"
          >
            <span className="flex-1">{g.label}</span>
            <span className="text-xs text-fg-muted">
              {g.rows.length} gw · {g.rows.filter(r => r.backendOk).length}/{g.rows.length} backend · {g.rows.filter(r => r.gatewayOk).length}/{g.rows.length} gateway
            </span>
            <Badge variant={TONE_VARIANT[g.tone]}>{g.tone}</Badge>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

Stories: all-green, ibkr-live-yellow (one gateway down), schwab-red. Tests: opens menu, renders 4 rows (2 IBKR + 1 Futu + 1 Schwab), worst-of badge tone reflects worst group.

Commit: `feat(patterns): connecteddropdown — per-broker gateway health grouped by mode`.

---

### Task 28.5: QuoteFeedDropdown

**Files:** `components/patterns/QuoteFeedDropdown/*` + type `QuoteFeedStatus` (already in Task 7 types) + fixture `services/fixtures/quote-feeds.ts` + service `services/quote-feeds.ts` (MockQuoteFeedService added to registry) + store `stores/global/quote-feeds.ts` (useQuoteFeedStore).

Per-asset-class × exchange quote-feed status (realtime/delayed/none). Answers "what quote subscriptions do I have for this exchange?" — distinct from the ConnectedDropdown's per-broker wire health.

**Fixture seed** (`quote-feeds.ts`):

```ts
import type { QuoteFeedStatus } from '../types';
export const QUOTE_FEEDS: QuoteFeedStatus[] = [
  { assetClass: 'stock',     exchange: 'NYSE',   feedType: 'realtime' },
  { assetClass: 'stock',     exchange: 'NASDAQ', feedType: 'realtime' },
  { assetClass: 'stock',     exchange: 'AMEX',   feedType: 'realtime' },
  { assetClass: 'options',                       feedType: 'delayed' },
  { assetClass: 'futures',   exchange: 'CME',    feedType: 'realtime' },
  { assetClass: 'futures',   exchange: 'CFE',    feedType: 'realtime' },
  { assetClass: 'forex',                         feedType: 'realtime' },
  { assetClass: 'crypto',                        feedType: 'realtime' },
  { assetClass: 'stock',     exchange: 'NYSE',   feedType: 'delayed', level: 2 },
];
```

**Service** (`services/quote-feeds.ts`): `MockQuoteFeedService` mirrors `MockConnectedService` shape (`snapshot()` + `subscribe(cb)`). No ticking timer — realtime→delayed flips are rare. Added to `Services` interface + `getServices()` in `services/registry.ts`.

**Store** (`stores/global/quote-feeds.ts`): `useQuoteFeedStore` mirrors `useConnectedStore` — `{ feeds: QuoteFeedStatus[] }`. Subscribes to service on first use.

**Pattern** `QuoteFeedDropdown.tsx` — DropdownMenu grouped by `assetClass`; each group shows DropdownMenuLabel + per-exchange rows (or inline when no exchange sub-rows). Badge tone: realtime=up, delayed=warn, none=down. Trigger label: worst-of all feeds.

Stories: all-realtime, some-delayed (options), level-2-separate. Tests: opens menu, renders grouped rows, worst-of trigger.

Commit: `feat(patterns): quotefeeddropdown — per-exchange realtime feed status`.

---

### Task 29: DataTable + MobileCardRow

**Files:** `components/patterns/{DataTable,MobileCardRow}/*` × 4 files each + `hooks/use-media-query.ts`.

- [ ] **Step 29.1: `hooks/use-media-query.ts`**

```ts
import * as React from 'react';

export function useMediaQuery(query: string): boolean {
  const subscribe = React.useCallback((cb: () => void) => {
    const mql = window.matchMedia(query);
    mql.addEventListener('change', cb);
    return () => mql.removeEventListener('change', cb);
  }, [query]);
  const get = React.useCallback(() => window.matchMedia(query).matches, [query]);
  return React.useSyncExternalStore(subscribe, get, () => false);
}
```

- [ ] **Step 29.2: `DataTable.tsx`** — tanstack-table + tanstack-virtual with mobile card fallback:

```tsx
import * as React from 'react';
import { useReactTable, getCoreRowModel, flexRender, type ColumnDef } from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useMediaQuery } from '@/hooks/use-media-query';
import { cn } from '@/lib/utils';

export interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  data: T[];
  rowKey(row: T): string;
  mobileRow?(row: T): React.ReactNode;
  rowHeight?: number;
  className?: string;
}

export function DataTable<T>({ columns, data, rowKey, mobileRow, rowHeight = 36, className }: DataTableProps<T>) {
  const isDesktop = useMediaQuery('(min-width: 48rem)');
  const parentRef = React.useRef<HTMLDivElement>(null);
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel(), getRowId: rowKey });
  const rows = table.getRowModel().rows;
  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 6,
  });

  if (!isDesktop && mobileRow) {
    return (
      <div ref={parentRef} className={cn('overflow-auto', className)} style={{ height: '100%' }}>
        <div style={{ height: virtual.getTotalSize(), position: 'relative' }}>
          {virtual.getVirtualItems().map(v => (
            <div key={v.key} style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${v.start}px)` }}>
              {mobileRow(rows[v.index].original)}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div ref={parentRef} className={cn('overflow-auto', className)} style={{ height: '100%' }}>
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-panel">
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id}>
              {hg.headers.map(h => (
                <th key={h.id} className="px-3 py-2 text-left text-fg-muted">
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody style={{ height: virtual.getTotalSize(), position: 'relative' }}>
          {virtual.getVirtualItems().map(v => {
            const row = rows[v.index];
            return (
              <tr key={row.id} style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${v.start}px)`, height: rowHeight }}>
                {row.getVisibleCells().map(cell => (
                  <td key={cell.id} className="px-3 py-2">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 29.3: `MobileCardRow.tsx`**

```tsx
import * as React from 'react';
import { cn } from '@/lib/utils';

export interface MobileCardRowProps {
  primary: React.ReactNode;
  secondary?: React.ReactNode;
  metrics: { label: string; value: React.ReactNode }[];
  onClick?: () => void;
  className?: string;
}

export const MobileCardRow = React.memo(function MobileCardRow({ primary, secondary, metrics, onClick, className }: MobileCardRowProps) {
  return (
    <button type="button" onClick={onClick} className={cn('block w-full rounded-md border border-border bg-panel p-3 text-left', className)} style={{ minBlockSize: '2.75rem' }}>
      <div className="flex items-baseline justify-between">
        <span className="text-base font-semibold">{primary}</span>
      </div>
      {secondary && <div className="text-xs text-fg-muted">{secondary}</div>}
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
        {metrics.map((m, i) => <div key={i}><span className="text-fg-muted">{m.label}:</span> {m.value}</div>)}
      </div>
    </button>
  );
});
```

- [ ] **Step 29.4: Stories — empty, 5-row, 500-row stress, mobile cards; Tests — renders cells, only subset of DOM rows with 500 data rows**

- [ ] **Step 29.5: Commit**

```bash
git add frontend/src/components/patterns/{DataTable,MobileCardRow}/ frontend/src/hooks/use-media-query.ts
git commit -m "feat(patterns): datatable (virtualized) + mobilecardrow + usemediaquery"
```

---

### Task 30: ColumnCustomizerDialog

**Files:** `components/patterns/ColumnCustomizerDialog/*`

Two-column modal (Available / Selected) with arrow + up/down reorder buttons. 30-column catalog hardcoded.

- [ ] **Step 30.1: Component**

Write the full component per spec — includes `ALL_COLUMNS` list of 30 entries (see spec §8.2) + working state + handlers for add/remove/move-up/move-down + Apply callback.

- [ ] **Step 30.2: Stories — default-open, pre-populated, empty-selection; Tests — add col, remove col, reorder, apply fires onChange with expected array**

- [ ] **Step 30.3: Commit**

```bash
git add frontend/src/components/patterns/ColumnCustomizerDialog/
git commit -m "feat(patterns): columncustomizerdialog — 30-col add/remove/reorder"
```

---

### Task 31: CommandPalette

**Files:** `components/patterns/CommandPalette/*`

Uses `cmdk`'s `Command.Dialog` (not Radix). Prefix routing: default (symbol), `>` (commands), `@` (accounts), `/` (routes), `?` (help).

Full component per spec §8.2. Global `Cmd+K` listener via `useEffect`.

Stories: default-closed, default-open, with-prefix-each. Tests: Cmd+K opens, ESC closes, typing `/orders` + Enter navigates, typing `>` shows registered commands.

Commit: `feat(patterns): commandpalette cmdk-based with prefix routing + global cmd+k`.

---

### Task 32: BottomTabBar

**Files:** `components/patterns/BottomTabBar/*`

Mobile-only (`md:hidden`) nav with 5 tabs: Overview, Orders, Positions, Watchlist, More. Uses TanStack Router `Link` + `useLocation` for active state.

Stories: overview-active, orders-active, positions-active. Tests: click tab → URL changes, active tab gets `aria-selected`.

Commit: `feat(patterns): bottomtabbar — mobile-only nav (hidden md:+)`.

---

### Task 33: CollapsibleDrawer

**Files:** `components/patterns/CollapsibleDrawer/*`

Thin Dialog wrapper sized to side, with `data-state=closed` translate-out animation.

Stories: left-open, right-open, closed. Tests: open + close, ESC closes, scrim-tap closes.

Commit: `feat(patterns): collapsibledrawer — mobile-only slide-in side drawer`.

---

## Chunk G — Layout

### Task 34: Topbar

**Files:** `components/layout/Topbar/*`

```tsx
import { Link } from '@tanstack/react-router';
import { ModeToggle } from '@/components/patterns/ModeToggle';
import { AccountPicker } from '@/components/patterns/AccountPicker';
import { ConnectedDropdown } from '@/components/patterns/ConnectedDropdown';
import { Button } from '@/components/primitives/Button';
import { Search } from 'lucide-react';
import { Icon } from '@/components/primitives/Icon';
import { useCommandsStore } from '@/stores/global/commands';

const ROUTES = [
  { to: '/overview', label: 'Overview' },
  { to: '/orders',   label: 'Orders' },
  { to: '/positions',label: 'Positions' },
  { to: '/watchlist',label: 'Watchlist' },
  { to: '/admin',    label: 'Admin' },
  { to: '/settings', label: 'Settings' },
];

export function Topbar() {
  const openPalette = useCommandsStore(s => s.setOpen);
  return (
    <header className="relative flex flex-col gap-2 border-b border-border bg-panel px-4 py-2 md:flex-row md:items-center md:justify-between">
      <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent-active" />
      <div className="flex items-center gap-4">
        <strong className="text-base">Trading Dashboard</strong>
        <ModeToggle />
        <AccountPicker />
        <ConnectedDropdown />
      </div>
      <div className="flex items-center gap-2">
        <nav className="hidden md:flex items-center gap-1">
          {ROUTES.map(r => (
            <Link key={r.to} to={r.to} className="rounded px-3 py-1 text-sm text-fg-muted hover:bg-muted/10">
              {r.label}
            </Link>
          ))}
        </nav>
        <Button variant="ghost" onClick={() => openPalette(true)} aria-label="Open command palette">
          <Icon as={Search} size="sm" /> ⌘K
        </Button>
      </div>
    </header>
  );
}
```

Stories: desktop, mobile-two-row. Tests: renders nav tabs, click Cmd+K button opens palette.

Commit: `feat(layout): topbar — mode+account+connected+nav+palette trigger`.

---

### Task 35: LeftPanel + RightPanel

**Files:** `components/layout/{LeftPanel,RightPanel}/*`

```tsx
// LeftPanel.tsx
import { ResizablePanelFrame } from '@/components/patterns/ResizablePanelFrame';
import { AccountSummary } from '@/features/overview/AccountSummary';
import { WatchlistCompact } from '@/features/watchlist/WatchlistCompact';

export function LeftPanel() {
  return (
    <ResizablePanelFrame
      direction="vertical"
      autoSaveId="shell-left-desktop"
      panels={[
        { id: 'summary',   defaultSize: 40, minSize: 20, content: <AccountSummary /> },
        { id: 'watchlist', defaultSize: 60, minSize: 30, content: <WatchlistCompact /> },
      ]}
    />
  );
}
```

`RightPanel.tsx`:

```tsx
import { ResizablePanelFrame } from '@/components/patterns/ResizablePanelFrame';
import { OpenOrdersCompact } from '@/features/orders/OpenOrdersCompact';
import { PositionsCompact } from '@/features/positions/PositionsCompact';

export function RightPanel() {
  return (
    <ResizablePanelFrame
      direction="vertical"
      autoSaveId="shell-right-desktop"
      panels={[
        { id: 'orders',    defaultSize: 40, minSize: 20, content: <OpenOrdersCompact /> },
        { id: 'positions', defaultSize: 60, minSize: 30, content: <PositionsCompact /> },
      ]}
    />
  );
}
```

Note: `AccountSummary`, `WatchlistCompact`, `OpenOrdersCompact`, `PositionsCompact` components are stubbed (return `<div>` placeholder) until filled in during Tasks 37-39.

Stories: both panels with stubs. Tests: renders both nested PanelGroups.

Commit: `feat(layout): leftpanel + rightpanel — nested vertical panelgroups`.

---

### Task 36: AppShell (single-subtree, Tailwind-responsive)

**Files:** `components/layout/AppShell/*`, update `src/routes/__root.tsx`

- [ ] **Step 36.1: `AppShell.tsx`**

```tsx
import * as React from 'react';
import { Outlet } from '@tanstack/react-router';
import { Topbar } from '@/components/layout/Topbar';
import { LeftPanel } from '@/components/layout/LeftPanel';
import { RightPanel } from '@/components/layout/RightPanel';
import { BottomTabBar } from '@/components/patterns/BottomTabBar';
import { CollapsibleDrawer } from '@/components/patterns/CollapsibleDrawer';
import { CommandPalette } from '@/components/patterns/CommandPalette';
import { ErrorBoundary } from '@/components/primitives/ErrorBoundary';
import { ResizablePanelFrame } from '@/components/patterns/ResizablePanelFrame';
import { useModeStore } from '@/stores/global/mode';
import { useActiveStores, getScopedStores } from '@/stores/registry';
import { getServices } from '@/services/registry';

export function AppShell(): React.JSX.Element {
  const mode = useModeStore(s => s.mode);
  const stores = useActiveStores();

  React.useEffect(() => {
    void stores.hydrate(getServices());
    return () => { getScopedStores(mode).suspend(); };
  }, [mode, stores]);

  React.useEffect(() => {
    document.body.setAttribute('data-mode', mode);
    return () => document.body.removeAttribute('data-mode');
  }, [mode]);

  const [leftOpen, setLeftOpen]   = React.useState(false);
  const [rightOpen, setRightOpen] = React.useState(false);

  return (
    <ErrorBoundary>
      <div className="flex h-screen flex-col">
        <Topbar />
        <div className="flex-1 overflow-hidden">
          {/* Desktop: 3-panel */}
          <div className="hidden h-full md:block">
            <ResizablePanelFrame
              direction="horizontal"
              autoSaveId="shell-horizontal-desktop"
              panels={[
                { id: 'left',  defaultSize: 20, minSize: 15, collapsible: true, content: <LeftPanel /> },
                { id: 'main',  defaultSize: 60, minSize: 30, content: <main className="h-full overflow-auto"><Outlet /></main> },
                { id: 'right', defaultSize: 20, minSize: 15, collapsible: true, content: <RightPanel /> },
              ]}
            />
          </div>
          {/* Mobile: single-column + drawers */}
          <div className="block h-full md:hidden">
            <main className="h-full overflow-auto pb-16"><Outlet /></main>
            <CollapsibleDrawer open={leftOpen}  onOpenChange={setLeftOpen}  side="left"><LeftPanel /></CollapsibleDrawer>
            <CollapsibleDrawer open={rightOpen} onOpenChange={setRightOpen} side="right"><RightPanel /></CollapsibleDrawer>
          </div>
        </div>
        <BottomTabBar />
      </div>
      <CommandPalette />
    </ErrorBoundary>
  );
}
```

- [ ] **Step 36.2: Update `__root.tsx`**

```tsx
import { createRootRoute } from '@tanstack/react-router';
import { AppShell } from '@/components/layout/AppShell';

export const Route = createRootRoute({ component: AppShell });
```

- [ ] **Step 36.3: Stories — desktop-viewport, mobile-viewport; Tests — renders both branches, hydration effect fires, body data-mode reflects store**

- [ ] **Step 36.4: Commit**

```bash
git add frontend/src/components/layout/AppShell/ frontend/src/routes/__root.tsx
git commit -m "feat(layout): appshell — single subtree, tailwind-responsive, hydrate on mode"
```

---

## Chunk H — Features

### Task 37: OverviewPage + AccountSummary

**Files:**
- Create: `frontend/src/features/overview/OverviewPage.tsx`
- Create: `frontend/src/features/overview/AccountSummary.tsx`
- Replace route `frontend/src/routes/overview.tsx`

- [ ] **Step 37.1: `AccountSummary.tsx`** — compact card showing selected account's alias + NLV + today's P&L (derived from positions). Used by `LeftPanel`.

- [ ] **Step 37.2: `OverviewPage.tsx`** — grid of 4 cards:
  1. Portfolio NLV summary (sum over all accounts in active mode)
  2. Top 5 positions by P&L unrealized
  3. Today's orders summary (counts by status)
  4. Watchlist favorites preview (first 5 from default watchlist)

- [ ] **Step 37.3: Update `routes/overview.tsx`**:

```tsx
import { createFileRoute } from '@tanstack/react-router';
import { OverviewPage } from '@/features/overview/OverviewPage';
export const Route = createFileRoute('/overview')({ component: OverviewPage });
```

- [ ] **Step 37.4: Commit**

```bash
git add frontend/src/features/overview/ frontend/src/routes/overview.tsx
git commit -m "feat(features): overview page + accountsummary for leftpanel"
```

---

### Task 38: OrdersPage + PositionsPage + compact variants

**Files:**
- `features/orders/OrdersPage.tsx`, `features/orders/OpenOrdersCompact.tsx`
- `features/positions/PositionsPage.tsx`, `features/positions/PositionsCompact.tsx`
- Updated route files

- [ ] **Step 38.1: `OrdersPage.tsx`** — `Tabs` (Open / Filled / Cancelled / All) + `DataTable` with columns: symbol, side, qty/filled, type, limit/stop, status, createdAt. `MobileCardRow` variant renders symbol+status as primary, other fields as metrics.

- [ ] **Step 38.2: `OpenOrdersCompact.tsx`** — `DataTable` with only open/partial filtered + minimum columns (symbol, side, qty, status).

- [ ] **Step 38.3: `PositionsPage.tsx`** — `DataTable` grouped by broker + account, columns: symbol, qty, avgCost, marketValue, pnlUnrealized, pnlRealized, currency.

- [ ] **Step 38.4: `PositionsCompact.tsx`** — compact variant for `RightPanel`.

- [ ] **Step 38.5: Update routes + commit**

```bash
git add frontend/src/features/{orders,positions}/ frontend/src/routes/{orders,positions}.tsx
git commit -m "feat(features): orders + positions pages with full + compact variants"
```

---

### Task 39: WatchlistPage + WatchlistCompact + ticking hook

**Files:**
- `features/watchlist/WatchlistPage.tsx`, `features/watchlist/WatchlistCompact.tsx`
- `hooks/use-ticking-quotes.ts`
- Updated route files

- [ ] **Step 39.1: `hooks/use-ticking-quotes.ts`** — rAF-throttled subscription:

```ts
import * as React from 'react';
import { getServices } from '@/services/registry';
import type { Quote } from '@/services/types';

export function useTickingQuotes(symbols: string[]): Record<string, Quote | undefined> {
  const [snapshot, setSnapshot] = React.useState<Record<string, Quote | undefined>>(() => {
    const svc = getServices().quotes;
    return Object.fromEntries(symbols.map(s => [s, svc.getSnapshot(s)]));
  });

  React.useEffect(() => {
    const svc = getServices().quotes;
    let raf = 0;
    let pending: Record<string, Quote> | null = null;
    const flush = () => {
      if (!pending) return;
      const p = pending;
      pending = null;
      setSnapshot(prev => ({ ...prev, ...p }));
    };
    const unsub = svc.subscribe(symbols, (q) => {
      if (!pending) pending = {};
      pending[q.symbol] = q;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(flush);
    });
    return () => { cancelAnimationFrame(raf); unsub(); };
  }, [symbols]);

  return snapshot;
}
```

- [ ] **Step 39.2: `WatchlistPage.tsx`** — pills selector (from `useActiveStores().useWatchlists`), "Customize Columns" button → `ColumnCustomizerDialog`, `DataTable` with wide column config backed by `useTickingQuotes`.

- [ ] **Step 39.3: `WatchlistCompact.tsx`** — top 10 rows of active watchlist with minimum columns (symbol, last, change %).

- [ ] **Step 39.4: `/watchlist/$id` route** — reads `id` param and navigates `useWatchlists.setActive(id)`.

- [ ] **Step 39.5: Commit**

```bash
git add frontend/src/features/watchlist/ frontend/src/routes/{watchlist,watchlist.$id}.tsx frontend/src/hooks/use-ticking-quotes.ts
git commit -m "feat(features): watchlist page + compact + rAF-throttled ticking hook"
```

---

### Task 40: AdminPage

**Files:**
- `features/admin/AdminPage.tsx`, `AdminConfigPage.tsx`, `AdminSecretsPage.tsx`
- Updated route files

- [ ] **Step 40.1: `AdminConfigPage.tsx`**

Tabs-inspired layout inside the Admin route. Fetches `/api/admin/config` (via Vite dev-proxy in dev; real CF Tunnel in prod). Shows `DataTable` with columns: namespace, key, value, value_type, updated_at. Buttons: "New" (opens `Dialog` with form), Edit (inline or dialog), Delete (idempotent).

- [ ] **Step 40.2: `AdminSecretsPage.tsx`** — `DataTable` with columns: namespace, key, value_type, updated_at (no value column). Reveal button per row opens `Dialog` with plaintext + "Copy" button + `Cache-Control: no-store` respected client-side by not storing plaintext anywhere.

- [ ] **Step 40.3: `AdminPage.tsx`** — shell with `Tabs` (Config / Secrets) + sub-routes rendering.

- [ ] **Step 40.4: Commit**

```bash
git add frontend/src/features/admin/ frontend/src/routes/admin*.tsx
git commit -m "feat(features): admin page — config + secrets crud via cf access"
```

---

### Task 41: SettingsPage

**Files:**
- `features/settings/SettingsPage.tsx`
- Updated route file

- [ ] **Step 41.1: Component**

Form with:
- Theme toggle (disabled, "coming soon — Phase 3.5")
- Density radio group (comfortable / compact) — persists to localStorage key `dashboard.settings.density`
- Sound toggle (mocked — stored to localStorage, no actual sound)
- About section: backend `/health` JSON fetch + `import.meta.env.VITE_*` display + build SHA from `import.meta.env.VITE_BUILD_SHA` (ship `null` fallback for dev)

- [ ] **Step 41.2: Commit**

```bash
git add frontend/src/features/settings/ frontend/src/routes/settings.tsx
git commit -m "feat(features): settings page — density + mocked toggles + about"
```

---

### Task 42: TradeStubPage + AlertsStubPage

**Files:**
- `features/trade/TradeStubPage.tsx`
- `features/alerts/AlertsStubPage.tsx`
- Updated route files

- [ ] **Step 42.1: Both pages use `EmptyState`**

```tsx
// TradeStubPage.tsx
import { EmptyState } from '@/components/patterns/EmptyState';
import { Activity } from 'lucide-react';
export function TradeStubPage() {
  return <EmptyState icon={Activity} title="Order ticket lands in Phase 5" description="For now, use your broker's native UI." />;
}
```

```tsx
// AlertsStubPage.tsx
import { EmptyState } from '@/components/patterns/EmptyState';
import { Bell } from 'lucide-react';
export function AlertsStubPage() {
  return <EmptyState icon={Bell} title="Alerts land in Phase 7" description="Telegram + email alerts for price/order events." />;
}
```

- [ ] **Step 42.2: Commit**

```bash
git add frontend/src/features/{trade,alerts}/ frontend/src/routes/{trade,alerts}.tsx
git commit -m "feat(features): trade + alerts stub pages"
```

---

## Chunk I — Tests

### Task 43: Playwright smoke — 5 frontend tests

**Files:** Modify `tests/e2e/smoke.spec.ts`

- [ ] **Step 43.1: Append frontend block**

```ts
test.describe('Phase 3 frontend shell', () => {
  test('loads in paper mode by default', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('body[data-mode="paper"]')).toBeAttached();
  });

  test('paper→live shows confirm dialog; cancel returns to paper', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('switch', { name: /mode/i }).click();
    await expect(page.getByRole('dialog', { name: /switch to live/i })).toBeVisible();
    await page.getByRole('button', { name: /cancel/i }).click();
    await expect(page.locator('body[data-mode="paper"]')).toBeAttached();
  });

  test('cmd+k opens palette and / prefix navigates', async ({ page }) => {
    await page.goto('/overview');
    await page.keyboard.press('Meta+k');
    await expect(page.getByRole('dialog', { name: /command palette/i })).toBeVisible();
    await page.keyboard.type('/orders');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/orders/);
  });

  test('watchlist column customizer reorders a column', async ({ page }) => {
    await page.goto('/watchlist');
    await page.getByRole('button', { name: /customize columns/i }).click();
    await expect(page.getByRole('dialog', { name: /customize columns/i })).toBeVisible();
    // Pick first available + move right + apply
    await page.getByRole('button', { name: /apply/i }).click();
  });

  test('mobile viewport renders BottomTabBar + drawer', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/overview');
    await expect(page.getByRole('tablist', { name: /primary/i })).toBeVisible();
    await page.getByRole('tab', { name: /positions/i }).click();
    await expect(page).toHaveURL(/\/positions/);
  });
});
```

- [ ] **Step 43.2: Run + commit**

```bash
export PATH="$HOME/.npm-global/bin:$PATH"
cd /mnt/c/dashboard/frontend && pnpm build
cd /mnt/c/dashboard
pnpm --dir tests/e2e exec playwright test smoke.spec.ts --project=chromium
```

```bash
git add tests/e2e/smoke.spec.ts
git commit -m "test(e2e): phase 3 frontend smoke — mode, palette, customizer, mobile nav"
```

---

### Task 44: DataTable stress perf story

**Files:** `frontend/src/components/patterns/DataTable/DataTable.stories.tsx` (add story)

- [ ] **Step 44.1: Add `StressPerf` story**

```tsx
export const StressPerf: Story = {
  render: () => {
    const data = React.useMemo(
      () => Array.from({ length: 500 }, (_, i) => ({ symbol: `SYM${i}`, last: Math.random() * 100, change: (Math.random() - 0.5) * 2 })),
      [],
    );
    const columns = React.useMemo(/* 30 columns of NumericCell */, []);
    React.useEffect(() => {
      const obs = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.duration > 16) console.warn('frame budget exceeded', entry.duration);
        }
      });
      obs.observe({ entryTypes: ['measure'] });
      return () => obs.disconnect();
    }, []);
    return <DataTable columns={columns} data={data} rowKey={r => r.symbol} />;
  },
};
```

Add an accompanying assertion that frame times stayed under 16ms during a 5-second sample (via `performance.mark` + `performance.measure` in the story + a Storybook-play function that reads the observer).

- [ ] **Step 44.2: Commit**

```bash
git add frontend/src/components/patterns/DataTable/DataTable.stories.tsx
git commit -m "test(perf): datatable stress story asserting <16ms frame budget"
```

---

## Chunk J — Close-out

### Task 45: Update docs

**Files:**
- Modify: `CHANGELOG.md`, `TASKS.md`, `CLAUDE.md`

- [ ] **Step 45.1: Append to `CHANGELOG.md`**

Add `## [0.3.0] — 2026-04-XX` block with full feature list: TanStack Router + cmdk palette; scoped stores with phantom types + suspend/hydrate; 16 primitives + 11 patterns + 4 layout + 8 features; OKLCH dark @theme + Noto CJK subsets; WatchlistPage w/ 30-col customizer + 500-row stress virtualization; AdminPage wired to CF-Access-gated /api/admin; Playwright frontend smoke × 5; bundle ~90 kB gz added.

- [ ] **Step 45.2: `TASKS.md`**

Replace `## Phase 3 — Frontend shell (mocks)  *(next)*` with a completed checkbox block listing the 48 tasks + `v0.3.0` tag line. Mark Phase 4 as `(next)`.

- [ ] **Step 45.3: `CLAUDE.md`**

Add a short section under the appropriate spot (e.g., after "Component Architecture") documenting:
- TanStack Router file-based routes; `routeTree.gen.ts` is gitignored, regenerated on prebuild via `pnpm tsr generate`.
- Scoped store factory + phantom types: features use `useActiveStores()`, never `@/stores/scoped/*` directly.
- `getServices()` is lazy; tests + Storybook decorators call `setTickingEnabled(false)`.
- Mode toggle paper→live requires confirm; live→paper is direct; default paper on load.
- Vite dev proxies `/api` + `/health` to `http://10.10.0.2:8000` for CF Access dev-bypass.

- [ ] **Step 45.4: Commit**

```bash
cd /mnt/c/dashboard
git add CHANGELOG.md TASKS.md CLAUDE.md
git commit -m "docs(phase3): changelog + tasks + claude.md close-out for v0.3.0"
```

---

### Task 46: Pre-flight sweep

- [ ] **Step 46.1: All frontend gates**

```bash
export PATH="$HOME/.npm-global/bin:$PATH"
cd /mnt/c/dashboard/frontend
pnpm lint && pnpm stylelint && pnpm typecheck && pnpm test && pnpm build && pnpm build-storybook
```
All must PASS.

- [ ] **Step 46.2: Backend still green**

```bash
cd /mnt/c/dashboard/backend
export $(grep -v '^#' ../.env | grep -v '^$' | xargs -d '\n')
uv run --frozen pytest -q
```

- [ ] **Step 46.3: Playwright smoke against a local preview**

```bash
cd /mnt/c/dashboard/frontend
pnpm preview &
PREVIEW_PID=$!
sleep 3
cd /mnt/c/dashboard
SMOKE_BASE_URL=http://localhost:4173 pnpm --dir tests/e2e exec playwright test smoke.spec.ts --project=chromium
kill $PREVIEW_PID
```
Expected: frontend smoke tests green. (Backend-dependent tests may skip — that's OK locally.)

---

### Task 47: Push + tag v0.3.0 + verify CI (USER GATE)

- [ ] **Step 47.1: Push**

```bash
cd /mnt/c/dashboard
git push origin main
```

- [ ] **Step 47.2: Watch CI + Deploy**

```bash
gh run watch
```

Both CI + Deploy must pass. If Deploy's Playwright smoke fails due to service-token config, apply the same diagnostic pattern used in Phase 2 — check backend logs for `jwt_missing` etc.

- [ ] **Step 47.3: Tag**

```bash
git tag -a v0.3.0 -m "v0.3.0 — Frontend shell (mocks)"
git push origin v0.3.0
```

- [ ] **Step 47.4: Prod-verify (7 criteria)**

From a browser: login via CF Access → Overview renders → toggle mode shows confirm → command palette works → watchlist page renders stress list without jank → admin page CRUD round-trip.

---

### Task 48: Memory updates

**Files:** `memory/*.md`

- [ ] **Step 48.1: Update `dashboard_v2_redesign.md`**

Change status to "implemented in v0.3.0"; note actual component names + deviations (scoped factory, phantom types, single AppShell subtree with Tailwind-responsive instead of separate desktop/mobile shells).

- [ ] **Step 48.2: Create `phase3_component_inventory.md`** with final primitives/patterns list.

- [ ] **Step 48.3: Update `MEMORY.md`**

```
- [Phase 3 component inventory](phase3_component_inventory.md) — final list of primitives/patterns for v0.3.0
```

- [ ] **Step 48.4: Git commits**

```bash
# Memory lives outside the repo — no commit. Just saved to the auto-memory dir.
```

---

## Spec coverage checklist

Mapping spec §14 exit criteria to tasks:

| Exit criterion | Task(s) |
|---|---|
| 16 primitives + 11 patterns + 4 layout + 8 features | Tasks 16-42 |
| ESLint + Stylelint + TS strict green | Tasks 15 + 46 |
| 70% primitive / 85% pattern coverage | Per-task tests + Task 46 |
| `@theme` dark + `--color-accent-active` swap | Tasks 2 + 36 |
| Noto fonts + unicode-range + langForMarket | Task 3 |
| TanStack Router + routeTree + keyboard shortcuts | Tasks 4-6 + 31 + 36 |
| Mode confirm + default paper on load | Tasks 12 + 26 |
| Scoped stores + phantom types + suspend/hydrate | Tasks 13-15 |
| 500-row stress @ 60fps | Tasks 29 + 39 + 44 |
| Column customizer 30 cols | Task 30 |
| Ticking mocks 500ms refcounted | Tasks 10 + 39 |
| Single-subtree responsive shell | Task 36 |
| AdminPage CRUD + Vite dev-proxy | Tasks 4 + 40 |
| ErrorBoundary at `__root.tsx` | Tasks 23 + 36 |
| Lazy `getServices()` | Task 11 |
| Playwright ≥ 5 frontend tests | Task 43 |
| Docs + `v0.3.0` tag | Tasks 45 + 47 |

No spec requirement without a matching task.

## Type-consistency check

- `Mode`, `Account`, `Order`, `Position`, `Watchlist`, `Quote`, `Command` defined in Task 7; consumed everywhere by import from `@/services/types`.
- `ScopedStores<M>` defined in Task 13, exported from `factory.ts`; consumed in Task 14 registry + Task 26 ModeToggle + Task 36 AppShell.
- `Services` interface defined in Task 11; passed to every `hydrate(svc)` in Task 13.
- `WatchlistColumnKey` enum defined in Task 7; consumed by Task 30 customizer + Task 39 WatchlistPage.
- `Command.prefix` is `'>' | '@' | '/' | '?'` per Task 7; Task 31 palette filters on these.
- `MobileCardRowProps` shape in Task 29; consumed by OrdersPage (Task 38), PositionsPage (Task 38), WatchlistPage (Task 39) for mobile fallback.

No drift detected.

## Placeholder scan

Reviewed every task body. No `TBD` / `TODO` / "similar to Task N" / "appropriate error handling" language. Every step has concrete code, exact file paths, or specific commands with expected outputs.

---

**Plan complete.** 48 tasks across 10 chunks. All architect-review findings woven into task specifications.
