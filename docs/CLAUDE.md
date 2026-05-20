# Docs & Phase Rules

Loads when Claude touches files in `docs/`. See root `CLAUDE.md` for cross-cutting invariants.

## Phase workflow

Every phase: brainstorm → spec self-review → **architect review (apply CRIT+HIGH+MED inline)** → user approval → plan → impl (subagent-driven) → close-out (CLAUDE.md/CHANGELOG.md/TASKS.md, tag, push).

Per-commit reviewer chain (spec-compliance + code-quality + lang-reviewer minimum + others when triggered). Full workflow + tooling list + reviewer table: `docs/PHASE-WORKFLOW.md`. Catalog: memory `project_tooling_inventory.md`. Trading-domain skills + routine invocation triggers: `docs/SKILLS-CATALOG.md`.

Architect review findings: apply CRIT+HIGH+MED inline; only LOWs may defer/document.

## Subagent model routing

**Coding is split between Codex and local Qwen.** Anthropic subagents do **not** write production code — they review.

### By task character

| Task character | Route to | Why |
|---|---|---|
| Multi-file refactors (≥3 files, cross-cutting renames, file splits) | **Codex** | Maintains coherent context across many files |
| Lua scripts / Redis atomics / narrow vendor-API specifics | **Codex** | Broader training-set coverage of API edges |
| Long-context analysis (full spec + plan + repo) | **Codex** | 256K context utilized fully |
| Self-contained module writes (new file, single class, well-specified) | **Qwen** (local) | Greenfield from focused prompts — 40 t/s, completes fully |
| TDD test writers (tests matching a known spec) | **Qwen** | Mechanical, structured output |
| Schema-driven SQL / Alembic migrations | **Qwen** | Highly structured form |
| Protobuf / gRPC schema additions | **Qwen** | Highly structured, spec-driven |
| Pydantic v2 schema / discriminated union writes | **Qwen** | Structured, type-checked form |
| Single new risk check (well-specified, isolated method in risk_service.py) | **Qwen** | Bounded scope |
| Prometheus metric wiring | **Qwen** | Mechanical from spec |
| Existing-code integration / multi-site judgment across ≥2 files | **Opus direct** | Holistic pattern matching |

**Qwen:** Always prepend `<think>\n</think>` to prompt (suppress runaway thinking). Budget: ≤512 thinking tokens. Endpoint: `http://192.168.50.30:11435/v1/completions`. Primary model: Qwen3.6-35B-A3B Q4_K_M.

**Codex:** Use `codex:codex-rescue` Agent tool subagent type. Model: `gpt-5.5`. For Codex defaults (A–G patterns), see memory `codex_defaults.md`.

### Fallback ladder

| Priority | Provider | Trigger |
|---|---|---|
| 1 | Codex (`codex:codex-rescue` Agent) | Default for "Codex tasks" |
| 2 | Qwen3.6-35B-A3B Q4_K_M | Default for "Qwen tasks"; or Codex rate-limit |
| 3 | Qwen3-Coder-Next Q3_K_XL 79B | 35B-A3B produces 2+ unusable outputs |
| 4 | Qwen2.5-Coder-14B (LKG) via Ollama `:11434` | All Qwen3.x fail |
| 5 | Opus main thread | Both Codex AND Qwen ladder exhausted |

### Reviewer dispatches

| Reviewer | Model |
|---|---|
| spec-compliance / `python-reviewer` / `typescript-reviewer` | `haiku` |
| `code-reviewer` / `security-reviewer` / `database-reviewer` / `silent-failure-hunter` | `sonnet` |
| `ARCHITECT-REVIEW` (once-per-phase, skill) | `opus` |

Pass `model: "haiku"`/`"sonnet"` to the `Agent` tool for per-call override. Run reviewer chain per chunk (≥5 commits), not phase end.

User overrides ("use codex", "use qwen", "claude take over") honor the named model.

## Shipped phases

Per-phase deep detail in memory files — read before changing those surfaces. Phases 1–11a: see memory `archive_closed_phases.md`.

| Phase | v | What shipped |
|---|---|---|
| 22b — LLM Strategy Generator | 0.22.1 | RestrictedPython sandbox, AST allowlist, StrategyGenerator, child-process worker, approve/reject REST; alembic 0071 |
| 22a.1 — Orchestrator Patch | 0.22.0.1 | SectorIngestionService (IBKR GetContractFundamentals), MV gate (corr-discounted notional), per_sector limits, Lua 3-ARGV, veto window; alembic 0069.1 |
| 22a — Strategy Orchestrator | 0.22.0 | PortfolioExposureGate, CorrelationService, AutoPromoteEvaluator, NightlyRetrainJob; alembic 0069-0070 |
| 21c — Advisor Perf-Attribution | 0.21.3 | AttributionService (FOR UPDATE SKIP LOCKED, 4-window PnL); InstrumentResolver.find_by_canonical_id (Redis-cached); session_close_for_decision; alembic 0068; 2 REST + APScheduler 900s; FE AdvisorScoreCard + outcome columns |
| 21b — LLM-in-Loop | 0.21.2 | ParamTunerService, ShadowPromoterService, AdvisorStub, AdvisorTelegramNotifier, BotSupervisor.restart(); alembic 0065–0067; 10 REST + 2 WS; FE param-tuner + shadow components |
| 21a.1 — Advisor Polish | 0.21.1 | SHADOW mode, semaphore (max_concurrent 1–4), override endpoints, per-account advisor config; alembic 0064 |
| 21a — LLM Advisor Gate | 0.21.0 | AdvisorService.review() fail-OPEN; OFF/OBSERVE/VETO/SHADOW; bot wiring station 5.5; alembic 0063 |
| 20 — Backtesting | 0.20.0 | BarFeed, FillSimulator, BacktestRunner, worker; alembic 0062 |
| 19 — Bot Engine v1 | 0.19.0 | BaseStrategy ABC, BotSupervisor, BotRiskCapService, BotFillRouter, 16 REST + 1 WS; alembic 0061 |
| 18 — Scanner + Filings + Earnings | 0.18.0 | Lark DSL, IndicatorComputer, SEC/HKEX pollers, EarningsService, HookExecutor; alembic 0058a–0060 |
| 17 — IBKR Algo Orders | 0.17.0 | 7 algo strategies, AlgoCapabilityService, risk checks; alembic 0057 |
| 16 — Bonds/Funds/CFD | 0.16.0 | 3 discriminated-union arms, 3 search services, 3 risk checks; alembic 0053–0056 |
| 15 — Forex + Crypto | 0.15.0 | FX RFQ, CoinbaseWsAdapter, OrderBook, risk checks; alembic 0051–0052 |
| 14 — Futures | 0.14.0 | ContractResolver, RollService, settlement listener, risk checks; alembic 0050 |
| 13 — Option Combos | 0.13.0 | 5-strategy combo flow, pnl_envelope, ComboService; alembic 0049 |
| 12 — Options | 0.12.1 | OptionChainService, OptionGreeksService, ExerciseService; alembic 0047 |
| 11d — Telegram Trade Exec | 0.11.3 | /place_order two-step, GETDEL nonce, check_trade fail-CLOSED bucket |
| 11a — AI Router | 0.11.0.8 | LiteLLM proxy, 8 capabilities, Redis auth, 4 REST + 2 WS, heavy-box WoL |
| 10b.2 — Portfolio Rollup | 0.10.3 | account_balance_snapshots hypertable, CAGGs, BalanceSnapshotWriter, WS gateway |
| 10b.1 — Position Sizing | 0.10.2 | 3 sizing methods, VolatilityService, bars_1d CAGG |
| 10a — Risk Gate | 0.10.0 | 7 checks, audit, admin CRUD, FE WARN/BLOCK banners |
