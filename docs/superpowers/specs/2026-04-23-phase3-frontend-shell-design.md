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
| 3 | Mode/account isolation | **Scoped store factory with phantom types + lifecycle contract** — `createScopedStores<M extends Mode>('live')` + `...('paper')` at startup; only the active scope has live subscriptions (`.suspend()` on inactive); `useActiveStores(): ScopedStores<Mode>` is the only import path | Structural guarantee that live and paper cannot mix; branded types prevent cross-scope reference holding; lifecycle contract prevents inactive-mode CPU/subscription leak (C1, H1). |
| 4 | Theme | **Dark-only** for v0.3.0; light toggle stubbed as "coming soon" in Settings | Trading UIs live in dark; doubling theme surface now is YAGNI. |
| 5 | Mode accent | **Logo + 2px Topbar underline** via `body[data-mode]` → `--color-accent-active` CSS var | Peripheral-vision loud, central-vision calm. Accent-everywhere fatigues. |
| 6 | Panel mechanics | **`react-resizable-panels`** + caret collapse button + `Cmd+[` / `Cmd+]` keyboard | Reuse solved drag + ARIA + keyboard + localStorage persistence (~3 kb). |
| 7 | Watchlist scope | **Wide catalog (~30 cols, TD-style)** + adapter (`WatchlistService` interface, localStorage impl in Phase 3) + **modal customizer** | Pro-tool fidelity; storage adapter keeps Phase-7+ backend swap clean. |
| 8 | Mode switch safety | **Confirm dialog on paper → live only**; live → paper is direct; **resets to paper on every page load** (not persisted) | Asymmetric risk: paper → live is the money-at-risk direction; "I thought I was still in paper" is exactly the foot-gun. |
| 9 | Mobile behavior | **Proper mobile UI via a SINGLE `AppShell` subtree** — Tailwind responsive classes show/hide desktop vs mobile chrome; `BottomTabBar` + `CollapsibleDrawer` + `MobileCardRow` always-mounted but `hidden` above `md`. 2.75 rem touch targets below `md` | Matches CLAUDE.md "mobile-first"; single subtree avoids remount state-loss when rotating across `md` (H3). |
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
    registry.ts             # getServices() — lazy construction; timers start on first subscribe (H6)
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
    primitives/             # 16: Button(existing) + Input Select Checkbox Radio
                            # Switch Dialog Popover DropdownMenu Tooltip Tabs
                            # Icon Badge Avatar Toast NumericCell ErrorBoundary  (+ErrorBoundary per M4)
    patterns/               # 11: DataTable MobileCardRow ColumnCustomizerDialog
                            # CommandPalette ModeToggle AccountPicker ConnectedDropdown
                            # ResizablePanelFrame CollapsibleDrawer BottomTabBar EmptyState
    layout/                 # 4: AppShell (ONE subtree, Tailwind-responsive — H3) Topbar LeftPanel RightPanel

  routes/                   # routeTree.gen.ts is generated by @tanstack/router-plugin
                            # — gitignored; regenerated via `pnpm tsr generate` prebuild (M6)

  features/
    overview/ orders/ positions/ watchlist/ admin/ settings/
    trade/  (stub)    alerts/ (stub)

  routes/                   # TanStack Router file-based; plugin writes routeTree.gen.ts (gitignored)
    __root.tsx              # mounts <ErrorBoundary> wrapping <Outlet /> (M4)
    index.tsx overview.tsx orders.tsx positions.tsx
    watchlist.tsx watchlist.$id.tsx admin.tsx admin.config.tsx admin.secrets.tsx
    settings.tsx trade.tsx alerts.tsx

  styles/
    tailwind.css            # @import + @theme block (dark-only)
    global.css              # @font-face Noto unicode-range routing

  App.tsx main.tsx
```

Each primitive + pattern ships `Component.tsx` + `Component.stories.tsx` + `Component.test.tsx` + `index.ts`.

## 5. Service layer

**Pattern:** every data concern exposes a TypeScript interface + a Phase-3 mock implementation. Singletons are **lazily constructed** via `getServices()` — no module-level side effects, no timers spin up at import time. Stores call through interfaces only.

```ts
// services/accounts.ts
export interface AccountsService {
  list(mode: Mode): Promise<Account[]>;
  subscribe(mode: Mode, cb: (accounts: Account[]) => void): () => void;
}
export class MockAccountsService implements AccountsService { /* fixtures-backed */ }

// services/quotes.ts — subscription-first; timer is lazy
export interface QuotesService {
  getSnapshot(symbol: string): Quote | undefined;
  subscribe(symbols: string[], cb: (q: Quote) => void): () => void;
  // Timer starts on first subscribe(); stops when refcount drops to 0.
  // setTickingEnabled(false) is a hard override for Storybook + Vitest.
  setTickingEnabled(on: boolean): void;
}

// services/registry.ts
let _services: Services | null = null;
export function getServices(): Services {
  if (_services) return _services;
  _services = {
    accounts: new MockAccountsService(),
    positions: new MockPositionsService(),
    orders: new MockOrdersService(),
    quotes: new MockQuotesService(),          // no timer yet
    watchlists: new LocalStorageWatchlistService(localStorage),
    connected: new MockConnectedService(),
    commands: new CommandRegistry(),
  };
  return _services;
}
export function resetServices(): void { _services = null; }  // Vitest/Storybook test hook
```

Storybook decorators + Vitest setup call `getServices().quotes.setTickingEnabled(false)` in `beforeAll`. `resetServices()` is available for tests that need a fully fresh singleton.

Boundary compliance: `services → lib` only. Fixtures live under `services/fixtures/` so they stay inside the services layer.

## 6. Store architecture

**Global stores** (cross-scope, one instance):

| Store | Fields | Notes |
|---|---|---|
| `useModeStore` | `mode: 'live'\|'paper'`, `pendingMode`, `status: 'idle'\|'switching'`, `setMode`, `requestModeSwitch(target)` | `requestModeSwitch('live')` triggers confirm dialog path. `status='switching'` during mid-flight hydrate (C2). Resets to `paper` + `status='idle'` on page load (not persisted). |
| `useThemeStore` | `theme: 'dark'` (light stubbed) | No-op for Phase 3 light toggle. |
| `useCommandStore` | `open`, `setOpen`, `commands: Command[]`, `register`, `unregister` | Features self-register via `useCommandsEffect`. |
| `useConnectedStore` | `statuses: ConnectedStatus[]` | Ticks health changes every few seconds (mocked). |

**Panel state** — no dedicated store. `react-resizable-panels` persists via `autoSaveId={\`shell-${viewport}\`}` so desktop ↔ mobile rotation doesn't clobber each other's sizes (H4). Since H3 makes the shell a single subtree with responsive-hidden mobile/desktop branches, the panels only mount inside the desktop branch — collapsing the issue surface further.

**Scoped factory with phantom types (H1):**

```ts
// stores/scoped/types.ts  — branded phantom preventing cross-scope reference holding
declare const brand: unique symbol;
type Scoped<M extends Mode, T> = T & { readonly [brand]: M };

// stores/factory.ts
export interface ScopedStores<M extends Mode> {
  readonly mode: M;
  useAccounts:    Scoped<M, UseBoundStore<StoreApi<AccountsState>>>;
  usePositions:   Scoped<M, UseBoundStore<StoreApi<PositionsState>>>;
  useOrders:      Scoped<M, UseBoundStore<StoreApi<OrdersState>>>;
  useWatchlists:  Scoped<M, UseBoundStore<StoreApi<WatchlistsState>>>;
  hydrate(svc: Services): Promise<void>;   // fire all slice hydrations in parallel
  suspend(): void;                          // cancel all subscriptions on slices
}

export function createScopedStores<M extends Mode>(mode: M): ScopedStores<M> { /* ... */ }

// stores/registry.ts — the ONLY place `createScopedStores` is called
const live  = createScopedStores('live');
const paper = createScopedStores('paper');

export function useActiveStores(): ScopedStores<Mode> {
  const mode = useModeStore(s => s.mode);
  return (mode === 'live' ? live : paper) as ScopedStores<Mode>;
}
```

**ESLint `boundaries` rule** — `stores/scoped/**` is only importable from `stores/factory.ts`. Features + layouts can only reach scoped state via `useActiveStores()`. Direct import of `createAccountStore` from a feature = CI red (H1).

**Hydration + suspend lifecycle (C1):**

- **At startup:** both `live` and `paper` are constructed but **neither is hydrated**. The active mode's scope is hydrated by an `<AppShell>` effect once the mode is resolved (default `paper`).
- **At mode-switch confirm:** `modeStore.setStatus('switching')` → `prevScope.suspend()` (cancels tick subscriptions on inactive scope) → `await nextScope.hydrate(getServices())` (parallel slice hydration) → `modeStore.setMode(next)` + `setStatus('idle')`. UI shows a lightweight skeleton while `status === 'switching'` (C2).
- **Inactive scope invariant:** `suspend()` leaves state readable but zero timers, zero event listeners. Test assertion: after `suspend()`, a store's internal `subscriptionCount` is 0.

Structural invariant: no singleton export of any scoped store; only the `live` + `paper` instances from the registry exist. Phantom brand prevents a feature from caching a reference across a mode flip.

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

### 8.1 Primitives (16) — boundary: `tokens + lib`

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
| `ErrorBoundary` | React class component | Catches route/feature render errors; falls back to `EmptyState` + "Reload" button; logs via `services.telemetry` (stub — Phase 3.5 wires real telemetry) (M4) |

### 8.2 Patterns (11) — boundary: `tokens + primitives + patterns + lib`

| Pattern | Composes | Purpose |
|---|---|---|
| `DataTable` | tanstack-table + tanstack-virtual + primitives | Sort/resize/reorder + virtualization; below `md` renders as `MobileCardRow` |
| `MobileCardRow` | Badge + NumericCell | Mobile card view for table rows |
| `ColumnCustomizerDialog` | Dialog + DnD list | Two-column modal (Available / Selected) for 30-col catalog |
| `CommandPalette` | `cmdk` `Command.Dialog` | Cmd+K palette with prefix routing. Uses `cmdk`'s built-in dialog (NOT Radix Dialog) to avoid focus-trap + scroll-lock double-owner bugs (M1) |
| `ModeToggle` | Switch + Badge | Paper↔Live with `requestModeSwitch` on paper→live |
| `AccountPicker` | DropdownMenu + Avatar | Grouped by broker; alias + NLV; selected highlight |
| `ConnectedDropdown` | DropdownMenu + Badge | Per-asset-class source health |
| `ResizablePanelFrame` | react-resizable-panels | Adds caret-collapse button + keyboard shortcut |
| `CollapsibleDrawer` | Dialog | Mobile swipe-in drawer |
| `BottomTabBar` | Tabs + Icon + Badge | Mobile nav |
| `EmptyState` | Icon + Button | Reusable empty UI |

### 8.3 Layout (4) — boundary: `tokens + primitives + patterns + layout + lib`

- `AppShell` — **ONE subtree** (H3). Uses Tailwind responsive classes (`hidden md:flex`, `md:hidden`) to show/hide desktop vs mobile chrome. Both mobile drawers + bottom-tab-bar AND desktop resizable panels are always in the DOM tree; visibility controlled by CSS. Rotating across `md` does NOT unmount — local state, table scroll position, and open drawers survive. Handles first-mount hydration of the active scope. Mounts `<CommandPalette>` + `<Toaster>` + top-level `<ErrorBoundary>`.
- `Topbar` — single row at `≥ md`, two rows at `< md` (logo+mode+account / navigation). Composes ModeToggle + AccountPicker + ConnectedDropdown + navigation tabs + Find symbol (opens palette). Tabs hidden below `md` (BottomTabBar takes over).
- `LeftPanel` — rendered only in the desktop branch of the AppShell tree (mobile uses `CollapsibleDrawer` with the same content). Nested vertical `PanelGroup`: account summary (top) + watchlist compact (bottom), draggable separator. Uses `autoSaveId="shell-left-desktop"` (H4).
- `RightPanel` — same pattern: desktop-only, `autoSaveId="shell-right-desktop"`. Nested vertical `PanelGroup`: open orders (top) + positions (bottom).

### 8.4 Features (8) — boundary: everything

| Feature | Purpose |
|---|---|
| `OverviewPage` | Portfolio NLV summary, top 5 positions by P&L, today's orders summary, watchlist favorites preview |
| `OrdersPage` | Full DataTable with tabs Open/Filled/Cancelled/All; row click opens order-detail popover |
| `PositionsPage` | Full DataTable, grouped by broker + account; right-click actions menu (stubs) |
| `WatchlistPage` | Selector pills + wide DataTable + Customize Columns button opens `ColumnCustomizerDialog`; stress list "500 Symbols" route |
| `AdminPage` | Tabs Config + Secrets wrapping Phase 2 `/api/admin/*`; reveal pops dialog with plaintext + copy + no-store. **Dev browser path:** Vite dev-proxy routes `/api/admin/*` directly to the backend at `http://10.10.0.2:8000` (NUC WG IP, bypasses nginx). This is required because CF-Access dev-bypass matches on the backend-observed source IP; through nginx the backend sees only nginx's internal IP and the bypass would fail (H5). **Prod:** browser → CF Tunnel → nginx → backend, with real CF Access JWT in `Cf-Access-Jwt-Assertion` header. |
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

**Fonts:** Phase 3 ships 3 Latin weights (Noto Sans 400/500/700) + **CJK Regular only** across 5 regions (TC, SC, HK, JP, KR) (H2). Bold CJK is synthesized via CSS `font-synthesis: weight` (already noted in CLAUDE.md). Each CJK region is **subset** to the Unified Ideographs block actually used by exchange symbol names — target `< 2 MB` per region after `glyphhanger` subsetting. Full CJK weights 500/700 promoted to Phase 3.5.

`@font-face` + `unicode-range` routes codepoints to the right variant at load-time. `langForMarket(exchange)` mapping:

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

One `<AppShell>` subtree across all viewports (H3). Desktop + mobile branches are both in the DOM; Tailwind responsive classes (`hidden md:flex`, `md:hidden`) show/hide. No remount on rotation.

| Viewport | Desktop branch visible | Mobile branch visible | Topbar | Panels | Tables | Nav |
|---|---|---|---|---|---|---|
| `≥ lg` | ✓ | hidden | Single row | 3-panel, draggable, collapsible | Full DataTable | Topbar tabs |
| `md–lg` | ✓ | hidden | Single row | Side panels auto-collapsed, restore via caret | Full DataTable | Topbar tabs |
| `sm–md` | hidden | ✓ | Two rows | Drawers via `CollapsibleDrawer` | MobileCardRow | `BottomTabBar` |
| `< sm` | hidden | ✓ | Compact two rows | Drawers | MobileCardRow condensed | `BottomTabBar` |

Touch-target rule: interactive elements `< md` must have `min-block-size: 2.75rem`. Enforced via manual review in Phase 3 (Stylelint can't do per-breakpoint natively; custom plugin is backlog).

**Mobile drawer UX:** `CollapsibleDrawer` slides in from left (account summary + compact watchlist) or right (orders + positions). Tap hamburger-like opener → animates in, overlays main content with scrim, ESC or scrim-tap closes. Swipe from edge also opens; swipe back closes.

## 11. Mode switch flow

**Paper → Live** (safety-gated, async-ordered per C2):

```
click ModeToggle (paper)
  → requestModeSwitch('live')
    → pendingMode='live', confirm dialog opens
      <ModeSwitchConfirmDialog> "Switching to LIVE mode. Real accounts and real orders will appear. Continue?"
        Cancel → pendingMode=null; mode stays 'paper'
        Confirm:
          → modeStore.setStatus('switching')            # UI shows skeleton from here
          → live.hydrate(getServices())                 # parallel slice hydration
          → await Promise.all(...)                      # all slices resolved
          → paper.suspend()                             # cancel inactive-scope subscriptions (C1)
          → modeStore.setMode('live')                   # flip
          → modeStore.setStatus('idle')                 # skeleton clears
          → Toast: "Switched to LIVE mode"
```

**Live → Paper** (direct, still async-safe):

```
click ModeToggle (live)
  → setStatus('switching')
  → paper.hydrate(getServices())
  → await Promise.all(...)
  → live.suspend()
  → setMode('paper')
  → setStatus('idle')
  → Toast: "Switched to paper mode"
```

`Cmd+Shift+M` runs the same `requestModeSwitch` flow — confirm gate fires from pointer and keyboard equally.

**Skeleton during `status === 'switching'`:** every feature route checks `useModeStore(s => s.status)` and renders a per-section `Skeleton` placeholder for the mid-flight window (typically < 100 ms for mocks, more meaningful once Phase 4+ real hydration lands). The Topbar mode indicator shows a subtle pulse on `--color-accent-active` during switching.

**Default at page load:** always `paper`, `status='idle'`. Not persisted.

## 12. Quote handling

Partial in Phase 3:

**Included:**
- `MockQuotesService` emits ticking prices on a ~500 ms interval via random-walk; timer is **lazy** (starts on first `subscribe()`, refcounted, stops at 0); `setTickingEnabled(false)` hard-overrides for Storybook + Vitest
- Quote type exposes ~30 fields driving the wide watchlist customizer
- `useTickingQuotes(symbols)` hook: per-symbol subscriptions via `useSyncExternalStore` — cells re-render only when their own symbol's value changes (M2)
- Cell components wrapped in `React.memo` keyed on `(symbol, field, formattedValue)` — skip identical renders even when a sibling cell updated
- Paint throttled to `requestAnimationFrame` — batch multiple 500 ms ticks that collide with a single frame
- Storybook includes a `DataTable/StressPerf` story rendering 30 cols × 500 rows × 500 ms ticking; asserts 60 fps via a PerformanceObserver marker
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
- Target (M3 split): **70% lines on primitives** (many are thin Radix wrappers with ~5 lines of prop-forwarding logic; hitting 85% forces trivial tests), **85% lines / 80% branches on patterns** (where real logic lives), lighter on features

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

- [ ] 16 primitives + 11 patterns + 4 layout + 8 features delivered with typed props, stories, tests
- [ ] ESLint boundaries green (incl. `stores/scoped/**` only-from-factory rule), Stylelint no-px green, TS strict green, backend lint/tests untouched and green
- [ ] 70 % line coverage on primitives, 85 % line / 80 % branch on patterns
- [ ] `@theme` dark block live; `--color-accent-active` swaps on `data-mode` change
- [ ] Noto `.woff2` shipped in `public/fonts/` — 3 Latin weights + 5 CJK regions × Regular only (subset < 2 MB per region); `@font-face` unicode-range correct; `langForMarket()` real mapping
- [ ] TanStack Router routes render; `routeTree.gen.ts` regenerated on prebuild via `pnpm tsr generate`; `Cmd+1..6` + `Cmd+K` + `Cmd+[` + `Cmd+]` all fire
- [ ] Mode toggle paper→live shows confirm dialog; live→paper direct; default paper on load; **during switch `status==='switching'` with skeleton UI until `Promise.all(hydrate())` resolves**
- [ ] Scoped live + paper stores independent; phantom types prevent cross-scope reference; `suspend()` cancels inactive-scope subscriptions; test asserts `subscriptionCount === 0` on inactive scope
- [ ] Watchlist page renders 500-row stress list at 60 fps via virtualization + `React.memo` cells + rAF paint throttle; Storybook perf story asserts the frame budget
- [ ] Column customizer supports add/remove/reorder across 30 cols; persists to localStorage
- [ ] Ticking mocks update UI ~500 ms; timer is refcounted (starts on first subscribe, stops at 0); `setTickingEnabled(false)` hard override for tests
- [ ] Single-tree responsive shell: `< md` shows mobile chrome, `≥ md` shows desktop chrome, rotation across `md` does NOT remount (local state survives)
- [ ] `AdminPage` performs CRUD against `/api/admin/config` + reveal via CF Access (dev uses Vite proxy → backend at 10.10.0.2:8000; prod uses CF Tunnel → nginx → backend)
- [ ] `<ErrorBoundary>` wraps every route's `<Outlet />` in `__root.tsx`
- [ ] `services.registry` is lazy (`getServices()`); no module-level timers spin up at import
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
- CSP hardening — Radix + Tailwind v4 require `style-src 'unsafe-inline'` or nonce plumbing; deferred until an explicit CSP pass (Phase 3.5 or later) (M5)

## 16. Risks for architect review

- **Scoped store factory memory** — two sets constructed but only one active; inactive is `suspend()`ed (zero timers, zero subscribers). Acceptable for Phase 3; real data in Phase 4+ will reuse the same lifecycle contract for streaming feeds.
- **TanStack Router file-based routes** — new API surface for subagents; `routeTree.gen.ts` generation required in prebuild step. HMR occasionally needs a full Vite restart when adding new route files (CLAUDE.md already notes WSL Vite restart discipline — L2).
- **Noto CJK font weight** — 5 regions × Regular only, subset per region to < 2 MB; bold synthesized via CSS. Full weight range deferred to Phase 3.5.
- **`cmdk` single-maintainer risk** — Paco Coursey's library. No direct Radix alternative. Accept risk. Uses `Command.Dialog` (not Radix Dialog) to avoid focus-trap double-owner.
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
| `@radix-ui/react-dialog` | Primitive (for Dialog primitive + CollapsibleDrawer + ColumnCustomizerDialog + ModeSwitchConfirmDialog; CommandPalette uses `cmdk`'s built-in Command.Dialog instead — M1) | 4 kB |
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
- CSP hardening — Radix + Tailwind style-src nonce/allowlist plumbing (M5)
- Full CJK weight range (500/700) — Phase 3.5 once we know which weights are actually in use
- Telemetry wiring — today `services.telemetry` is a stub that `<ErrorBoundary>` calls; Phase 3.5 routes to a real sink

## 19. Architect-review applied

Adversarial pass dispatched via `everything-claude-code:architect` on 2026-04-23 against spec commit `f74246f`. Verdict was ⚠️ REVISE; all CRITICAL + HIGH applied inline, all MEDIUM applied inline (none documented-without-fix), LOW findings L1 + L3 dropped as noted, L2 merged into §16 risks.

| # | Sev | Issue | Fix applied (spec section) |
|---|---|---|---|
| C1 | CRITICAL | Inactive-scope ticks leak CPU (+ future Phase-4 double-subscribe) | `ScopedStores.suspend()` contract + `hydrate()` + refcounted lazy ticking timer (§6, §5, §11, §12) |
| C2 | CRITICAL | Mode-switch race: `useActiveStores()` flips before target scope hydrates — user sees empty live UI | `modeStore.status: 'idle'\|'switching'`; flip only after `Promise.all(hydrate())` resolves; features render skeleton while switching (§6, §11) |
| H1 | HIGH | "Never-mix" is discipline, not types — features can stash a cross-scope reference | Phantom-typed `ScopedStores<M>` + ESLint rule `stores/scoped/**` importable only from `stores/factory.ts` (§3, §6) |
| H2 | HIGH | CJK 5 regions × 3 weights = ~45 MB woff2 | Regular only + `font-synthesis: weight` for bold + glyphhanger subset per region to < 2 MB; 500/700 deferred to Phase 3.5 (§9, §14, §18) |
| H3 | HIGH | `AppShellDesktop`/`AppShellMobile` two-tree swap loses state on `md` rotate | Single `<AppShell>` subtree; Tailwind responsive classes toggle branches; both branches always mounted (§3, §8.3, §10) |
| H4 | HIGH | `react-resizable-panels` autoSaveId collision across viewports | Panels only mount in desktop branch (ties into H3); per-side `autoSaveId="shell-{left\|right}-desktop"` (§6, §8.3) |
| H5 | HIGH | Dev browser → nginx → backend: bypass matcher sees nginx IP, not browser's WG peer IP → 401 | Vite dev-proxy routes `/api/admin/*` directly to backend `http://10.10.0.2:8000` in dev (bypasses nginx); prod uses real CF Access JWT (§8.4) |
| H6 | HIGH | `services/registry.ts` module-level `new MockQuotesService()` starts timer at import, breaks test isolation | Lazy `getServices()` + `resetServices()`; timer starts on first `subscribe()`, refcount drops to 0 stops it (§5, §14) |
| M1 | MEDIUM | `cmdk` + Radix Dialog focus-trap double-owner | Use `cmdk`'s built-in `Command.Dialog`; Radix Dialog still used for other dialogs (§8.2, §17) |
| M2 | MEDIUM | 30-col × 500-row × 500 ms tick: cell-level re-render cost | `useSyncExternalStore` per symbol + `React.memo` cells + rAF paint throttle + Storybook perf story asserts 60 fps (§12, §14) |
| M3 | MEDIUM | 85 % primitive coverage forces trivial render tests on thin Radix wrappers | 70 % primitives / 85 % patterns split (§13, §14) |
| M4 | MEDIUM | No error-boundary strategy — feature render throws crash the shell | `<ErrorBoundary>` primitive; wraps `<Outlet />` in `__root.tsx`; falls back to `EmptyState` + reload (§8.1, §4) |
| M5 | MEDIUM | No CSP note for Radix/Tailwind inline styles | Documented as explicit OUT (§15) + deferred in §18 |
| M6 | MEDIUM | TanStack Router `routeTree.gen.ts` gitignore + prebuild requirement missing | Documented in §4 + §14 + §16 |
| L1 | LOW | Asymmetric split-color pending cue is novelty UX for an already-safe confirm dialog | Dropped; replaced with a subtle `--color-accent-active` pulse during `status==='switching'` (§11) |
| L2 | LOW | TanStack Router HMR discipline | Merged into §16 risks |
| L3 | LOW | `nginx /metrics` doesn't belong in §18 (that's a Phase 2.1 concern) | Removed from §18 |

**Outcome:** spec revised from ⚠️ REVISE to ✅ READY. No findings left unaddressed.
