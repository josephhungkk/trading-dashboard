# Phase 3 — Frontend Shell (mocks) — Design

**Status:** approved 2026-04-23 · target tag `v0.3.0`

## 1. Goal

Ship the v2 UI shell end-to-end with realistic mocks: routing, state, theming, three-panel desktop + mobile bottom-tab-bar responsive layout, multi-watchlist with wide-catalog customizer, ticking quote mocks, CF-Access-gated admin CRUD UI — enough scaffolding that Phase 4+ broker adapters can land without touching the shell. No real broker data.

## 2. Context

- Phase 0 scaffold landed the component-boundary ESLint rules (`tokens → primitives → patterns → layout → features`, plus `services`, `stores`, `hooks`, `lib`) with only `Button` as a primitive.
- Phase 1 put the prod HTTP stack behind CF Tunnel + CF Access.
- Phase 2 shipped CF Access JWT verification + `app_config` / `app_secrets` DB-backed runtime config with the `/api/admin/*` + `/metrics` routes.
- The operator locked visual direction in memory `dashboard_v2_redesign.md` on 2026-04-19: strict live|paper mode separation, three-panel desktop layout (left summary + watchlist split by draggable slidebar, main, right orders + positions), Connected dropdown for per-asset-class quote-source status, multi-watchlist with fully-customizable columns in a modal, Noto Sans UI + Noto Mono numbers + `langForMarket()` CJK routing, delayed-quote background tint, invisible scrollbars with hover reveal.
- Phase 9 will swap the plaintext `DATABASE_URL` password for PG client-cert auth over WireGuard (out of scope here).

## 3. Architecture decisions

Locked during brainstorm, in order asked:

| # | Decision | Choice | Rationale (short) |
|---|---|---|---|
| 1 | Router | **TanStack Router** (file-based) | Type-safe routes, built-in search-param state. Trade larger API surface for compile-time correctness. |
| 2 | Data strategy | **Pluggable adapters** — one TS interface per concern, mock impl now, HTTP impl when Phase 4+ backend exists | Avoids speculative URL contracts; keeps frontend/backend decoupled until real wiring. |
| 3 | Mode/account isolation | **Scoped store factory** — `createScopedStores('live')` + `createScopedStores('paper')` at startup; `useActiveStores()` selects one | Structural guarantee that live and paper data cannot mix; one type per slice, two instances. |
| 4 | Theme | **Dark-only** for v0.3.0; light toggle stubbed as "coming soon" in Settings | Trading UIs live in dark; doubling theme surface now is YAGNI. |
| 5 | Mode accent | **Logo + 2px Topbar underline** via `body[data-mode]` → `--color-accent-active` CSS var | Peripheral-vision loud, central-vision calm. Accent-everywhere fatigues. |
| 6 | Panel mechanics | **`react-resizable-panels`** + caret collapse button + `Cmd+[` / `Cmd+]` keyboard | Reuse solved drag + ARIA + keyboard + localStorage persistence (~3 kb). |
| 7 | Watchlist scope | **Wide catalog (~30 cols, TD-style)** + adapter (`WatchlistService` interface, localStorage impl in Phase 3) + **modal customizer** | Pro-tool fidelity; storage adapter keeps Phase-7+ backend swap clean. |
| 8 | Mode switch safety | **Confirm dialog on paper → live only**; live → paper is direct; **resets to paper on every page load** (not persisted) | Asymmetric risk: paper → live is the money-at-risk direction; "I thought I was still in paper" is exactly the foot-gun. |
| 9 | Mobile behavior | **Proper mobile UI** — below `md`: `BottomTabBar` + swipe drawers for panels + `MobileCardRow` for tables + 2.75 rem touch targets | Matches CLAUDE.md "mobile-first" directive; desktop 3-panel retrofitted as mobile shrunk is not acceptable. |
| 10 | Mocks richness | **Realistic** — ~50 symbols across NYSE/NASDAQ/SEHK/TSE/KRX/FX/crypto, 6 accounts (2 per broker × 3 brokers), ~30 positions, ~20 orders with mixed statuses, 4 named watchlists + 1 stress list of 500 generated tickers | Exercises every edge case (CJK routing, multi-broker grouping, delayed-quote tinting, asset-class tabs) without theater. |
| 11 | Command palette | **Full `cmdk`-based Cmd+K** with prefix routing (`>` commands, `@` accounts, `/` routes, `?` help) | Infra cost is once; every new command is near-free after. Pro-tool expectation. |

## 4. Directory layout

```
frontend/src/
  design-tokens/            # expanded with mode-accent + oklch dark tokens
    colors.ts spacing.ts typography.ts radii.ts motion.ts index.ts

  lib/
    formatters.ts           # money, percent, number, locale-aware
    cn.ts                   # existing clsx + tailwind-merge
    cmd-match.ts            # fuzzy matcher for command palette

  services/
    api.ts ws.ts lang.ts    # existing; lang.ts gets real mapping
    accounts.ts positions.ts orders.ts quotes.ts watchlists.ts commands.ts connected.ts
    registry.ts             # singleton services object
    fixtures/
      brokers.ts accounts.ts symbols.ts positions.ts orders.ts watchlists.ts

  stores/
    global/
      mode.ts theme.ts commands.ts connected.ts
    scoped/
      account-store.ts positions-store.ts orders-store.ts watchlists-store.ts
    factory.ts              # createScopedStores(mode)
    registry.ts             # singleton { live, paper } + useActiveStores()

  hooks/
    use-media-query.ts use-mode-scoped.ts use-shortcut.ts
    use-ticking-quotes.ts use-toast.ts

  components/
    primitives/             # 15: Button(existing) + Input Select Checkbox Radio
                            # Switch Dialog Popover DropdownMenu Tooltip Tabs
                            # Icon Badge Avatar Toast NumericCell
    patterns/               # 11: DataTable MobileCardRow ColumnCustomizerDialog
                            # CommandPalette ModeToggle AccountPicker ConnectedDropdown
                            # ResizablePanelFrame CollapsibleDrawer BottomTabBar EmptyState
    layout/                 # 4: AppShell (+Desktop/Mobile split) Topbar LeftPanel RightPanel

  features/
    overview/ orders/ positions/ watchlist/ admin/ settings/
    trade/  (stub)    alerts/ (stub)

  routes/                   # TanStack Router file-based
    __root.tsx index.tsx overview.tsx orders.tsx positions.tsx
    watchlist.tsx watchlist.$id.tsx admin.tsx admin.config.tsx admin.secrets.tsx
    settings.tsx trade.tsx alerts.tsx

  styles/
    tailwind.css            # @import + @theme block (dark-only)
    global.css              # @font-face Noto unicode-range routing

  App.tsx main.tsx
```

Each primitive + pattern ships `Component.tsx` + `Component.stories.tsx` + `Component.test.tsx` + `index.ts`.

## 5. Service layer

**Pattern:** every data concern exposes a TypeScript interface + a Phase-3 mock implementation. Singletons live in `services/registry.ts`. Stores call through interfaces only.

```ts
// services/accounts.ts
export interface AccountsService {
  list(mode: Mode): Promise<Account[]>;
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void;
}
export class MockAccountsService implements AccountsService { /* fixtures-backed */ }

// services/quotes.ts — subscription-first
export interface QuotesService {
  getSnapshot(symbol: string): Quote | undefined;
  subscribe(symbols: string[], cb: (q: Quote) => void): () => void;
  setTickingEnabled(on: boolean): void;   // Storybook + tests call with false
}

// services/registry.ts
export const services = {
  accounts: new MockAccountsService(),
  positions: new MockPositionsService(),
  orders: new MockOrdersService(),
  quotes: new MockQuotesService(),
  watchlists: new LocalStorageWatchlistService(localStorage),
  connected: new MockConnectedService(),
  commands: new CommandRegistry(),
} as const;
```

Storybook decorators + Vitest setup call `services.quotes.setTickingEnabled(false)` to silence ticks during snapshots.

Boundary compliance: `services → lib` only. Fixtures live under `services/fixtures/` so they stay inside the services layer.

## 6. Store architecture

**Global stores** (cross-scope, one instance):

| Store | Fields | Notes |
|---|---|---|
| `useModeStore` | `mode: 'live'\|'paper'`, `pendingMode`, `setMode`, `requestModeSwitch(target)` | `requestModeSwitch('live')` triggers confirm dialog path. Resets to `paper` on page load (not persisted). |
| `useThemeStore` | `theme: 'dark'` (light stubbed) | No-op for Phase 3 light toggle. |
| `useCommandStore` | `open`, `setOpen`, `commands: Command[]`, `register`, `unregister` | Features self-register via `useCommandsEffect`. |
| `useConnectedStore` | `statuses: ConnectedStatus[]` | Ticks health changes every few seconds (mocked). |

**Panel state** — no dedicated store. `react-resizable-panels` has its own `autoSaveId` localStorage persistence.

**Scoped factory:**

```ts
// stores/factory.ts
export interface ScopedStores {
  useAccounts: UseBoundStore<StoreApi<AccountsState>>;
  usePositions: UseBoundStore<StoreApi<PositionsState>>;
  useOrders: UseBoundStore<StoreApi<OrdersState>>;
  useWatchlists: UseBoundStore<StoreApi<WatchlistsState>>;
}

export function createScopedStores(mode: Mode): ScopedStores {
  return {
    useAccounts: createAccountStore(mode),
    usePositions: createPositionsStore(mode),
    useOrders: createOrdersStore(mode),
    useWatchlists: createWatchlistsStore(mode),
  };
}

// stores/registry.ts
const live = createScopedStores('live');
const paper = createScopedStores('paper');

export function useActiveStores(): ScopedStores {
  const mode = useModeStore(s => s.mode);
  return mode === 'live' ? live : paper;
}
```

**Hydration** — each scoped store exposes `hydrate(service)` that populates state from its matching service. Called from `<AppShell>` effect on mount and on mode change. Structural invariant: no singleton export of any scoped store; only the `live` + `paper` instances from the registry exist.

## 7. Routing + command palette

**Routes (TanStack Router file-based):**
```
/                    → redirect /overview
/overview /orders /positions /watchlist /watchlist/:id /admin /admin/config /admin/secrets
/settings /trade (stub) /alerts (stub)
```

`<AppShell>` wraps `<Outlet />`; side panels sibling to `<Outlet />` (state persists across route changes).

**Command palette (`cmdk`):**

- Always mounted at `__root`
- Global `Cmd+K` toggle
- Prefix-routed: default (symbol search), `>` (slash commands), `@` (accounts), `/` (routes), `?` (shortcuts cheat sheet)
- Features register their own commands via `useCommandsEffect` on mount; unregister on unmount
- Initial command set (~15): find symbol, switch mode, new watchlist, customize columns, collapse panel, open route × 6, reveal secret (admin only), copy API base URL, sign out (placeholder), keyboard shortcuts

**Keyboard shortcuts:**
| Shortcut | Action | Scope |
|---|---|---|
| `Cmd+K` | Open palette | Global |
| `Cmd+[` / `Cmd+]` | Collapse left/right panel (desktop) | Global |
| `Cmd+Shift+M` | Toggle mode (paper→live gated by confirm) | Global |
| `Cmd+1..6` | Jump to route 1..6 | Global |
| `Esc` | Close palette/dialog/drawer | Contextual |
| `/` | Focus search (current page) | Contextual |
| `?` | Open palette with `?` prefix | Global |

## 8. Components

### 8.1 Primitives (15) — boundary: `tokens + lib`

| Component | Built on | Responsibility |
|---|---|---|
| `Button` (existing) | Radix Slot + `cva` | Variants default/destructive/outline/ghost/link; sizes sm/md/lg |
| `Input` | native | Text + number; `variant="numeric"` right-aligns + Noto Mono |
| `Select` | Radix | Grouped options |
| `Checkbox` | Radix | Label composition |
| `Radio` | Radix RadioGroup | |
| `Switch` | Radix | Settings + filter chips |
| `Dialog` | Radix | Overlay + ESC close |
| `Popover` | Radix | |
| `DropdownMenu` | Radix | Nested menus (account picker, column-header) |
| `Tooltip` | Radix | |
| `Tabs` | Radix | Navigation |
| `Icon` | `lucide-react` | Normalize size (1/1.25/1.5 rem) + `aria-label` |
| `Badge` | div | Variants: neutral, live, paper, delayed, up, down, warn |
| `Avatar` | Radix | Initials-only fallback (no external images) |
| `Toast` | Radix | Top-right stack via `useToast` |
| `NumericCell` | span | Right-aligned Noto Mono; `emphasis="up\|down\|neutral"` tints fg |

### 8.2 Patterns (11) — boundary: `tokens + primitives + patterns + lib`

| Pattern | Composes | Purpose |
|---|---|---|
| `DataTable` | tanstack-table + tanstack-virtual + primitives | Sort/resize/reorder + virtualization; below `md` renders as `MobileCardRow` |
| `MobileCardRow` | Badge + NumericCell | Mobile card view for table rows |
| `ColumnCustomizerDialog` | Dialog + DnD list | Two-column modal (Available / Selected) for 30-col catalog |
| `CommandPalette` | `cmdk` + Dialog | Cmd+K palette with prefix routing |
| `ModeToggle` | Switch + Badge | Paper↔Live with `requestModeSwitch` on paper→live |
| `AccountPicker` | DropdownMenu + Avatar | Grouped by broker; alias + NLV; selected highlight |
| `ConnectedDropdown` | DropdownMenu + Badge | Per-asset-class source health |
| `ResizablePanelFrame` | react-resizable-panels | Adds caret-collapse button + keyboard shortcut |
| `CollapsibleDrawer` | Dialog | Mobile swipe-in drawer |
| `BottomTabBar` | Tabs + Icon + Badge | Mobile nav |
| `EmptyState` | Icon + Button | Reusable empty UI |

### 8.3 Layout (4) — boundary: `tokens + primitives + patterns + layout + lib`

- `AppShell` — picks `<AppShellDesktop>` or `<AppShellMobile>` via `useMediaQuery('(min-width: 48rem)')`. First-mount store hydration. Mounts `<CommandPalette>` + `<Toaster>`.
- `Topbar` — two rows on mobile, one row on desktop. Composes ModeToggle + AccountPicker + ConnectedDropdown + navigation tabs + Find symbol (opens palette).
- `LeftPanel` — nested vertical `PanelGroup`: account summary (top) + watchlist compact (bottom), draggable separator.
- `RightPanel` — nested vertical `PanelGroup`: open orders (top) + positions (bottom).

### 8.4 Features (8) — boundary: everything

| Feature | Purpose |
|---|---|
| `OverviewPage` | Portfolio NLV summary, top 5 positions by P&L, today's orders summary, watchlist favorites preview |
| `OrdersPage` | Full DataTable with tabs Open/Filled/Cancelled/All; row click opens order-detail popover |
| `PositionsPage` | Full DataTable, grouped by broker + account; right-click actions menu (stubs) |
| `WatchlistPage` | Selector pills + wide DataTable + Customize Columns button opens `ColumnCustomizerDialog`; stress list "500 Symbols" route |
| `AdminPage` | Tabs Config + Secrets wrapping Phase 2 `/api/admin/*`; reveal pops dialog with plaintext + copy + no-store |
| `SettingsPage` | Theme toggle disabled ("coming soon"), density toggle real, sound toggle mocked, backend `/health` + env + build info |
| `TradeStubPage` | "Phase 5" placeholder |
| `AlertsStubPage` | "Phase 7" placeholder |

## 9. Theming

Single source of truth: `src/styles/tailwind.css` `@theme` block. TS tokens in `design-tokens/*.ts` mirror CSS vars for Storybook + Vitest; a `design-tokens.test.ts` asserts parity.

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
  --color-positive:     oklch(70% 0.17 145);
  --color-negative:     oklch(62% 0.19 25);
  --color-warn:         oklch(78% 0.15 75);
  --color-info:         oklch(70% 0.12 230);

  /* Mode accents */
  --color-accent-live:  oklch(58% 0.20 25);
  --color-accent-paper: oklch(72% 0.18 75);
  --color-accent-active: var(--color-accent-paper);  /* default paper */

  /* Status tints */
  --color-delayed-bg:   oklch(22% 0.02 240);
  --color-delayed-fg:   oklch(60% 0.02 240);

  /* Fonts */
  --font-sans:  "Noto Sans", system-ui, sans-serif;
  --font-mono:  "Noto Sans Mono", ui-monospace, monospace;

  /* Type scale */
  --text-xs: 0.75rem;  --text-sm: 0.875rem;  --text-base: 1rem;
  --text-lg: 1.125rem; --text-xl: 1.25rem;   --text-2xl: 1.5rem;
  --text-3xl: 1.875rem;
}

body[data-mode="live"]  { --color-accent-active: var(--color-accent-live); }
body[data-mode="paper"] { --color-accent-active: var(--color-accent-paper); }
```

`<AppShell>` effect reads `useModeStore(s => s.mode)` and sets `document.body.setAttribute('data-mode', mode)`.

**Fonts:** Phase 3 ships 3 Latin weights + 5 CJK regional variants (TC, SC, HK, JP, KR) via `@font-face` + `unicode-range`. `langForMarket(exchange)` mapping:

| Exchange | `lang` |
|---|---|
| NYSE, NASDAQ, AMEX, ARCA, CBOE | `en` |
| SEHK | `zh-HK` |
| TSE (Tokyo) | `ja` |
| KRX | `ko` |
| TWSE | `zh-TW` |
| SSE, SZSE | `zh-CN` |
| LSE, EURONEXT, XETRA | `en` (default) |

## 10. Responsive

Breakpoints (Tailwind defaults):
- `sm` — 40 rem · `md` — 48 rem · `lg` — 64 rem · `xl` — 80 rem · `2xl` — 96 rem

| Viewport | Shell | Topbar | Panels | Tables | Navigation |
|---|---|---|---|---|---|
| `≥ lg` | Desktop | Single row | 3-panel, draggable, collapsible | Full DataTable | Topbar tabs |
| `md–lg` | Desktop | Single row | Side panels auto-collapsed, restore via caret | Full DataTable | Topbar tabs |
| `sm–md` | Mobile | Two rows | Drawers via `CollapsibleDrawer` | MobileCardRow | `BottomTabBar` |
| `< sm` | Mobile | Compact two rows | Drawers | MobileCardRow condensed | `BottomTabBar` |

Touch-target rule: interactive elements `< md` must have `min-block-size: 2.75rem`. Enforced via manual review in Phase 3 (Stylelint can't do per-breakpoint natively; custom plugin is backlog).

**Mobile drawer UX:** `CollapsibleDrawer` slides in from left (account summary + compact watchlist) or right (orders + positions). Tap hamburger-like opener → animates in, overlays main content with scrim, ESC or scrim-tap closes. Swipe from edge also opens; swipe back closes.

## 11. Mode switch flow

**Paper → Live** (safety-gated):

```
click ModeToggle (paper)
  → requestModeSwitch('live')
    → pendingMode='live', dialog opens
      <ModeSwitchConfirmDialog> "Switching to LIVE mode. Real accounts and real orders will appear. Continue?"
        Cancel → pendingMode=null, mode='paper' (no change)
        Confirm → setMode('live')
          → useActiveStores() returns `live` scoped stores
            → <AppShell> effect re-hydrates live stores from services
              → Toast: "Switched to LIVE mode"
```

**Live → Paper** (direct, no dialog):

```
click ModeToggle (live)
  → setMode('paper')
    → stores swap + re-hydrate + Toast: "Switched to paper mode"
```

`Cmd+Shift+M` runs the same `requestModeSwitch` — confirm gate fires from pointer and keyboard equally.

**Visual pending cue:** while the confirm dialog is open, the Topbar mode-accent strip shows a 2-color split (current | pending) as a micro-cue that something is mid-flight.

**Default at page load:** always `paper`. Not persisted.

## 12. Quote handling

Partial in Phase 3:

**Included:**
- `MockQuotesService` emits ticking prices on a ~500 ms interval via random-walk; disable via `setTickingEnabled(false)` for Storybook + Vitest
- Quote type exposes ~30 fields driving the wide watchlist customizer
- `useTickingQuotes(symbols)` hook subscribes per-component via EventTarget — no global re-render
- Delayed-quote tint on `isDelayed: true` rows via `--color-delayed-*` vars
- `ConnectedDropdown` shows mocked per-asset-class source health

**Deferred to broker-adapter phases:**
- Real market-data subscriptions — IBKR TWS (Phase 4), Futu OpenD (Phase 6), Schwab streamer (Phase 8)
- Quote-source failover, snapshot-vs-streaming budget, rate-limit handling
- Historical OHLC bars (for klineschart) — Phase 4+
- Options chains, Level 2 depth, time & sales

**Clean handoff:** `QuotesService` interface is the swap point. Phase 4 replaces `MockQuotesService` with `IbkrQuotesService` that routes through the backend WebSocket. Downstream components unchanged.

**Ticking realism:** uniform 500 ms ticks in Phase 3 (Poisson-distributed gaps + "news" jumps = backlog polish, not needed for shell demo).

## 13. Testing

**Unit (Vitest + RTL):**
- Every primitive + pattern has colocated `.test.tsx`
- Variant renders, callbacks fire, ARIA roles present, keyboard navigation works
- Target: **85% lines / 80% branches** on primitives + patterns; features get lighter coverage

**Storybook:**
- Every primitive + pattern has `.stories.tsx` with 2-4 variants
- States: default, hover, focused, disabled, loading, empty, error (where applicable)
- CJK content story for NumericCell + DataTable
- Long/short content stories for DataTable + MobileCardRow overflow
- `a11y` addon flags contrast + ARIA violations automatically

**E2E smoke (Playwright):**
- Extend existing `tests/e2e/smoke.spec.ts` with 5+ frontend tests:
  1. Loads in paper mode by default
  2. Mode toggle paper→live shows confirm dialog; Cancel returns to paper
  3. Cmd+K palette opens, search navigates to route
  4. Watchlist column customizer reorders columns
  5. Mobile viewport renders BottomTabBar + drawer
- Runs in CI only (no post-deploy smoke for these; Phase 4+ adds post-deploy once auth flow is fully testable)

**Boundary compliance:** `eslint-plugin-boundaries` runs on every `pnpm lint`. Dedicated `eslint-boundaries.test.ts` asserts common unsafe imports fail lint.

**Accessibility:** WCAG 2.2 AA across primitives + patterns. Features not audited in v0.3.0 (too much surface); fix-forward.

**Keyboard-only:** tabbing reaches every interactive element in the shell (Topbar → LeftPanel → main route → RightPanel).

**Visual regression (Chromatic):** deferred to Phase 3.5.

## 14. Exit criteria (definition of done)

- [ ] 15 primitives + 11 patterns + 4 layout + 8 features delivered with typed props, stories, tests
- [ ] ESLint boundaries green, Stylelint no-px green, TS strict green, backend lint/tests untouched and green
- [ ] 85 % line / 80 % branch coverage on primitives + patterns
- [ ] `@theme` dark block live; `--color-accent-active` swaps on `data-mode` change
- [ ] Noto `.woff2` shipped in `public/fonts/`; `@font-face` unicode-range correct; `langForMarket()` real mapping
- [ ] TanStack Router routes render; `Cmd+1..6` + `Cmd+K` + `Cmd+[` + `Cmd+]` all fire
- [ ] Mode toggle paper→live shows confirm dialog; live→paper direct; default paper on load
- [ ] Scoped live + paper stores independent; mode switch re-hydrates; no cross-mode read possible
- [ ] Watchlist page renders 500-row stress list at 60 fps via virtualization
- [ ] Column customizer supports add/remove/reorder across 30 cols; persists to localStorage
- [ ] Ticking mocks update UI ~500 ms (disable-able)
- [ ] Mobile `< md` renders `AppShellMobile` with BottomTabBar + drawers + card tables
- [ ] `AdminPage` performs CRUD against `/api/admin/config` + reveal via CF Access
- [ ] Playwright smoke extends with ≥ 5 frontend tests, green in CI
- [ ] `CHANGELOG.md`, `TASKS.md`, `CLAUDE.md` updated; `v0.3.0` tagged

## 15. Scope boundaries — explicit OUT

- Real broker data (Phase 4/6/8)
- Login/auth flow in the frontend (CF Access cookie is the only auth)
- Chart rendering (klineschart — Phase 4+ when real OHLC exists)
- Order ticket execution (Phase 5)
- Alerts UI (Phase 7)
- Server-side persistence of settings (theme/density/sound stay localStorage)
- BottomTabBar badge notifications are static from fixtures, no live count update
- Drag-and-drop between watchlists (later phase)
- Offline / service-worker caching
- i18n of UI chrome (English chrome; symbol names use `lang` attr for font routing)
- Animations beyond Radix defaults (no Framer Motion)

## 16. Risks for architect review

- **Scoped store factory memory** — two full sets of stores load simultaneously. Mock fixtures ~50 kB JSON. Acceptable for Phase 3; real data in Phase 4+ needs streaming hydration to avoid double-subscribe.
- **TanStack Router file-based routes** — new API surface for subagents; learning curve in implementation.
- **Noto CJK font weight** — ~15 `.woff2` files (5 regions × 3 weights). Mitigation: `font-display: swap` + `unicode-range` routing loads only needed variants per page.
- **`cmdk` single-maintainer risk** — Paco Coursey's library. No direct Radix alternative. Accept risk.
- **Touch-target rule enforcement** — Stylelint can't enforce `min-block-size` per-breakpoint natively. Manual review in Phase 3; custom plugin is backlog.
- **`react-resizable-panels` SSR concern** — N/A; SPA only.

## 17. Dependencies added

Approximate gzipped footprint:

| Package | Purpose | gzip (approx) |
|---|---|---|
| `zustand` | State | 2 kB |
| `@tanstack/react-router` + `router-devtools` | Routing | 20 kB |
| `@tanstack/react-table` | Table state | 15 kB |
| `@tanstack/react-virtual` | Virtualization | 5 kB |
| `react-resizable-panels` | Panel dividers | 3 kB |
| `cmdk` | Command palette | 8 kB |
| `@radix-ui/react-dialog` | Primitive | 4 kB |
| `@radix-ui/react-popover` | Primitive | 4 kB |
| `@radix-ui/react-dropdown-menu` | Primitive | 5 kB |
| `@radix-ui/react-tooltip` | Primitive | 3 kB |
| `@radix-ui/react-tabs` | Primitive | 3 kB |
| `@radix-ui/react-select` | Primitive | 5 kB |
| `@radix-ui/react-checkbox` | Primitive | 2 kB |
| `@radix-ui/react-radio-group` | Primitive | 2 kB |
| `@radix-ui/react-switch` | Primitive | 2 kB |
| `@radix-ui/react-toast` | Primitive | 4 kB |
| `@radix-ui/react-avatar` | Primitive | 2 kB |
| `lucide-react` | Icon set (tree-shaken) | ~1 kB per icon × ~20 icons |
| **Total** | | **~90 kB gzipped** |

## 18. Phase-3 non-goals deferred to Phase 3.5

Tracked so they don't disappear:
- Chromatic visual regression
- Stylelint custom plugin for breakpoint-aware touch-target enforcement
- More realistic ticking (Poisson gaps, news jumps)
- BottomTabBar badge live-count wiring (waits for Phase 4+ real data)
- Storybook Chromatic CI run
- nginx `location = /metrics` (recorded in Phase 2.1 follow-up; can ride along here)

## 19. Architect-review applied

_To be populated after Step 3 of the phase workflow (adversarial ARCHITECT-REVIEW pass). CRITICAL + HIGH findings applied inline; MEDIUM fix-or-document; LOW deferred with explicit rationale. Record the findings table here before the user spec-approval gate._
