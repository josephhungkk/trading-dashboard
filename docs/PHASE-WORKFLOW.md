# Phase Workflow

Every phase follows this exact sequence:

1. **Brainstorm** — `superpowers:brainstorming` skill. Produces `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`.
2. **Spec self-review** — placeholder scan, internal consistency, scope check, ambiguity check. Fix inline.
3. **Architect review** — invoke the user-scope `ARCHITECT-REVIEW` skill adversarially on the spec. It returns findings ranked CRITICAL / HIGH / MEDIUM / LOW with concrete "change X to Y" recommendations. **Apply all CRITICAL + HIGH + MEDIUM findings before proceeding.** Only LOWs may defer or document. (Project rule established 2026-04-28; see memory `feedback_architect_findings_apply_through_medium.md`.) Record the findings table in the spec under an "Architect review — applied" section.
4. **User spec approval.**
5. **Writing plans** — `superpowers:writing-plans` skill. Produces `docs/superpowers/plans/YYYY-MM-DD-<topic>-plan.md`.
6. **Implementation** — `superpowers:subagent-driven-development` (preferred) or `superpowers:executing-plans`.
7. **Phase close-out** — update `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md`; tag `vN.N.N`; commit + push.

**Skip step 3 only for truly trivial phases (single-file refactors, docs-only changes).** Phases 2–9 all get the architect review. Phase 0 skipped it (~2 hrs of preventable CI debugging as a result); Phase 1 used it (10 findings, all fixed before implementation). Worth the ~5-min round-trip.

## Proactive tooling (run without being asked, every phase)

**Step 0 — Research & reuse (before any new code):** `gh search repos`, `gh search code`, Context7 MCP (`resolve-library-id` + `query-docs`) for primary docs, `everything-claude-code:search-first` skill, Exa MCP for broad research, check `/mnt/c/Dashboard_old/` for portable artifacts. Adopt or port over writing from scratch.

**Step 1 — Brainstorm:** `superpowers:brainstorming` (terminal state = invoke writing-plans). Use `everything-claude-code:council` for ambiguous tradeoffs; sequential-thinking MCP for tricky multi-step decisions.

**Step 3 — Architect review:** `ARCHITECT-REVIEW` skill (user-scope) — always. For high-stakes phases add `SENIOR-ARCHITECT` skill or `everything-claude-code:santa-method` (dual-voice adversarial). Record findings in spec's "Architect review — applied" section.

**Step 5 — Writing plans:** `superpowers:writing-plans` + the shipped `plan-document-reviewer-prompt.md` template. `everything-claude-code:planner` agent as alternative draft helper.

**Step 6 — Implementation (per-task review chain at every commit boundary):**

| Order | Tool | Triggers on |
|---|---|---|
| 1 | Implementer subagent (uses `superpowers:subagent-driven-development/implementer-prompt.md`) | every task |
| 2 | Spec compliance reviewer (uses `…/spec-reviewer-prompt.md`) | every task |
| 3 | Code quality reviewer (uses `…/code-quality-reviewer-prompt.md`) | every task |
| 4 | Language-specific review: `everything-claude-code:python-reviewer` OR `everything-claude-code:typescript-reviewer` | backend vs frontend |
| 5 | `everything-claude-code:security-reviewer` | auth / secrets / user-input / crypto paths |
| 6 | `everything-claude-code:database-reviewer` | schema / migration / SQL paths |
| 7 | `everything-claude-code:type-design-analyzer` | Pydantic / TS strict surfaces |
| 8 | `everything-claude-code:silent-failure-hunter` | async paths, critical flows (Phase 4+ broker adapters especially) |
| 9 | `everything-claude-code:a11y-architect` | frontend UI changes (Phase 3+) |
| 10 | `everything-claude-code:build-error-resolver` | when `pnpm build` / `uv run` / `docker compose build` fails |
| 11 | `everything-claude-code:tdd-guide` or `superpowers:test-driven-development` | when writing new features or tests fail |
| 12 | `everything-claude-code:pr-test-analyzer` | before merging PR once real test suites exist |

**Reviews fire at the end of every chunk** (≥5 substantive commits), per `feedback_review_per_chunk.md`. Per-task review is optional and reserved for high-risk tasks (e.g. auth/payments → security; migrations → database). Velocity-driven skipping below the chunk boundary is forbidden — every chunk gets at minimum spec-compliance + code-quality + the relevant language reviewer before the next chunk begins. Conditional reviewers (#5–#11) fire when their trigger surface is touched. End-of-phase: spec-compliance reviewer alone before tag. Pre-existing tests passing is NOT a substitute for a fresh reviewer pass — tests prove the wire didn't change, not that the code is well-built or matches the spec.

If a review batch is skipped during a session, **catch up before the next chunk begins** — never carry unreviewed commits into the next layer (Chunk B reads schema written by A; Chunk E reads contract written by B; finding a shape bug after dependents land is much more expensive than at the commit boundary). Spec + code-quality + language reviewers can be dispatched in parallel against the unreviewed range, but the catch-up MUST happen before any new feature work.

**Step 7 — Close-out:** `superpowers:finishing-a-development-branch`; `claude-md-management:claude-md-improver` for CLAUDE.md updates; `everything-claude-code:doc-updater` for README/docs refresh; `commit-commands:*` for structured commits; `gh run view` to watch CI.

## Per-phase subject anchors

Invoke these skills when the current phase's subject area is being touched:

| Phase | Skills |
|---|---|
| **Phase 2 (current)** | `POSTGRES-BEST-PRACTICES`, `POSTGRESQL`, `everything-claude-code:postgres-patterns`, `everything-claude-code:database-migrations`, `SQL-INJECTION-TESTING`, `BACKEND-SECURITY-CODER`, `everything-claude-code:security-review`, `API-DESIGN-PRINCIPLES`, `everything-claude-code:api-design`, `everything-claude-code:python-patterns`, `everything-claude-code:python-testing`, `everything-claude-code:tdd-workflow`, `everything-claude-code:verification-loop` |
| Phase 3 shell | `/frontend-design`, `REACT-PATTERNS`, `REACT-STATE-MANAGEMENT`, `UI-UX-PRO-MAX`, `RADIX-UI-DESIGN-SYSTEM`, `FRONTEND-UI-DARK-TS`, `FRONTEND-SECURITY-CODER`, `everything-claude-code:frontend-patterns`, `everything-claude-code:e2e-testing`, `everything-claude-code:accessibility` |
| Phase 4-6, 8 adapters | `API-DESIGN-PRINCIPLES`, `BACKEND-ARCHITECT`, `ARCHITECTURE-PATTERNS`, `everything-claude-code:mcp-server-patterns`, `POWERSHELL-WINDOWS` (NUC ops glue) |
| Phase 7 AI | `ai:building-pydantic-ai-agents`, `LLM-APP-PATTERNS`, `LLM-EVALUATION`, `LLM-APPLICATION-DEV-AI-ASSISTANT`, `everything-claude-code:eval-harness`, `everything-claude-code:ai-regression-testing`, `everything-claude-code:cost-aware-llm-pipeline`, `everything-claude-code:pytorch-patterns` |

## Always-on (every tool call, every session)

- **Hooks auto-fire:** `gateguard` (fact-force on Write/Edit/destructive Bash — comply with its preamble, don't fight), `commitlint` (lowercase subject; ≤100 char body lines; never `--no-verify`), `gitleaks` (secret scan), `continuous-learning-v2:observe` (captures patterns), `remember:SessionStart` (reloads session state).
- **Rules auto-inject:** 24 files from `~/.claude/rules/` + `PYTHON/` + `TYPESCRIPT/` subdirs.
- **Memory auto-loads:** `MEMORY.md` + subject-matched memories — in particular `project_tooling_inventory.md` (the full catalog) + `feedback_proactive_tooling.md` (this discipline).
- **MCP servers live and ready:** `chrome-devtools`, `playwright`, `context7`, `github`, `memory`, `sequential-thinking`, `exa`. Load via ToolSearch when a task calls for them.

Full catalog + user/project scope scan results live in memory at `project_tooling_inventory.md`. Consult that FIRST before reaching for the global catalog of 250+ skills and 48 agents.

**Trading-domain skills with phase-surface triggers and routine chains:** [`docs/SKILLS-CATALOG.md`](docs/SKILLS-CATALOG.md). Read this when starting any phase that touches forex/crypto, autonomous bots, LLM signal generation, or data infrastructure.
