# Skills, Plugins & Agents Catalog

Reference for all skills/agents wired into this project. Skills live in the `ecc` fork at
`~/.claude/plugins/marketplaces/ecc/skills/`. Invoke via `Skill` tool using the `ecc:` prefix
(or the original namespace shown in parentheses where the fork re-exports them unchanged).

See `docs/PHASE-WORKFLOW.md` for the base reviewer chain. This file adds the trading-domain
and AI/bot-specific layer on top of it.

---

## Routine chains (fire automatically, like the reviewer chain)

These run at fixed points in every phase — no need to ask.

### Per-chunk (end of every chunk, alongside spec-compliance + code-quality + lang reviewers)

| Skill / Agent | Namespace | Fires when |
|---|---|---|
| `security-reviewer` | `everything-claude-code` | Any chunk touching auth, secrets, order path, user input |
| `database-reviewer` | `everything-claude-code` | Any chunk touching schema / migration / SQL |
| `ecc:safety-guard` | `ecc` | Any chunk that adds or modifies an autonomous order placement path |
| `ecc:llm-trading-agent-security` | `ecc` | Any chunk wiring an LLM to order execution (Phase 16+) |

### Per-phase close-out (after final chunk, before tag)

| Skill / Agent | Namespace | Purpose |
|---|---|---|
| `ecc:observability-designer` | `ecc` | Verify Prometheus metrics coverage for new services |
| `ecc:data-quality-auditor` | `ecc` | Audit any new market data feed (OHLCV, FX rates, Greeks) |
| `ecc:security-review` | `ecc` | Full security pass before tagging |
| `ecc:benchmark` | `ecc` | Baseline latency/throughput before shipping any hot path |

### Per-session start (when continuing an active phase)

| Skill | Fires when |
|---|---|
| `superpowers:executing-plans` | Resuming mid-phase implementation |
| `ecc:prompt-governance` | Session touches AI router prompts or capability map |

---

## Phase-surface triggers (invoke when the named surface is touched)

### Phase 15 — Forex / Crypto

| Skill | Trigger surface |
|---|---|
| `ecc:data-quality-auditor` | Any new FX rate feed, crypto OHLCV ingestion |
| `ecc:sql-database-assistant` | TimescaleDB CAGGs for FX/crypto tick data |
| `ecc:migration-architect` | Alembic migrations for forex/crypto schema |
| `ecc:llm-trading-agent-security` | Any autonomous FX/crypto order path |
| `ecc:safety-guard` | Live order placement gates |

### Phase 16+ — Autonomous trading bots / signal generation

| Skill | Trigger surface |
|---|---|
| `ecc:eval-harness` | **Before writing any bot** — define pass/fail evals first |
| `ecc:agent-workflow-designer` | Designing signal → decision → order pipeline |
| `ecc:autonomous-agent-harness` | Wiring persistent agent loop with crons + memory |
| `ecc:agent-harness-construction` | Defining tool/action space for a trading agent |
| `ecc:autonomous-loops` | DAG or sequential multi-step signal flows |
| `ecc:agent-designer` | Multi-agent architecture (ensemble, competing strategies) |
| `ecc:agenthub` | Running parallel competing strategy agents |
| `ecc:enterprise-agent-ops` | 24/7 bot lifecycle, observability, security boundaries |
| `ecc:autoresearch-agent` | Self-refining parameter tuning loop (edit → eval → commit/reset) |
| `ecc:council` | Multi-LLM ensemble voting for signal decisions |
| `ecc:statistical-analyst` | A/B testing strategy variants, signal significance |

### LLM / AI router (Phase 11a surface, ongoing)

| Skill | Trigger surface |
|---|---|
| `ecc:prompt-governance` | Adding/modifying AI router prompts or capability map |
| `ecc:cost-aware-llm-pipeline` | Optimising token spend across 8 capabilities |
| `ecc:llm-cost-optimizer` | Routing decisions, model selection per task |
| `ecc:eval-harness` | Evaluating AI router output quality (pass@k) |
| `ecc:ai-regression-testing` | After any model upgrade in the capability map |
| `ecc:rag-architect` | Building retrieval over strategy docs / broker API docs |
| `ecc:iterative-retrieval` | RAG-style lookups over instrument/strategy DB |

### Data infrastructure

| Skill | Trigger surface |
|---|---|
| `ecc:data-scraper-agent` | Adding a new scheduled market data source |
| `ecc:exa-search` | REALTIME_SENTIMENT capability, news/macro signal inputs |
| `ecc:clickhouse-io` | If backtesting scale outgrows TimescaleDB |
| `ecc:performance-profiler` | FastAPI hot paths, high-frequency position updates |
| `ecc:sql-database-assistant` | TimescaleDB CAGG query optimisation |

### LLM training / model development (Phase 19+)

| Skill | Trigger surface |
|---|---|
| `ecc:pytorch-patterns` | Custom signal model training pipelines |
| `ecc:eval-harness` | Training eval criteria before model work begins |
| `ecc:benchmark` | Model performance comparison (Qwen vs Codex vs Claude) |
| `ecc:agent-eval` | Head-to-head agent quality comparison |
| `ecc:continuous-learning-v2` | Instinct accumulation from every trade/agent decision |

### Finance / strategy analysis

| Skill | Trigger surface |
|---|---|
| `ecc:financial-analyst` | Fundamental signal generation, DCF, ratio analysis |
| `ecc:business-investment-advisor` | Capital allocation decisions, ROI/IRR on strategy variants |
| `ecc:statistical-analyst` | Backtesting win rates, Sharpe, drawdown significance |
| `ecc:research-ops` | Researching new strategy components or asset classes |
| `ecc:deep-research` | Sector/macro research for AI signal context |
| `ecc:market-research` | Competitor platforms, new broker/data vendor due diligence |

### Infrastructure / DevOps

| Skill | Trigger surface |
|---|---|
| `ecc:deployment-patterns` | CI/CD changes, blue-green rollout |
| `ecc:migration-architect` | Zero-downtime TimescaleDB schema changes |
| `ecc:tech-debt-tracker` | Reviewing deferred items in CLAUDE.md before phase planning |
| `ecc:mcp-server-builder` | Exposing broker APIs as MCP tools for LLM agents |
| `ecc:mcp-server-patterns` | MCP server design for any new external integration |
| `ecc:observability-designer` | Extending Prometheus metrics coverage |

---

## How to invoke

**In a message:** just name the skill and what you want:
```
use ecc:autoresearch-agent to set up the parameter tuning loop for the FX strategy
apply ecc:eval-harness before we start the signal generation agent
```

**I should auto-invoke** when the trigger surface above is touched — you don't need to ask.

**Namespace note:** skills copied from upstream ECC into the fork are invoked as `ecc:<name>`.
Skills still only in upstream use `everything-claude-code:<name>`. Finance/engineering skills
copied from `claude-code-skills` are also under `ecc:<name>` in the fork.

---

## Full skill inventory (74 skills in fork)

```
agent-designer              agent-eval                  agent-harness-construction
agent-workflow-designer     agenthub                    agentic-engineering
ai-regression-testing       api-design                  architecture-decision-records
autonomous-agent-harness    autonomous-loops             autoresearch-agent
backend-patterns            benchmark                   blueprint
browser-qa                  business-investment-advisor  clickhouse-io
coding-standards            configure-ecc               context-budget
continuous-agent-loop       continuous-learning          continuous-learning-v2
cost-aware-llm-pipeline     council                     data-quality-auditor
data-scraper-agent          database-migrations          deep-research
deployment-patterns         docker-patterns              documentation-lookup
e2e-testing                 enterprise-agent-ops         eval-harness
exa-search                  financial-analyst            frontend-patterns
gateguard                   git-workflow                 github-ops
hookify-rules               iterative-retrieval          liquid-glass-design
llm-cost-optimizer          llm-trading-agent-security   market-research
mcp-server-builder          mcp-server-patterns          migration-architect
observability-designer      performance-profiler         postgres-patterns
production-scheduling       prompt-governance            python-patterns
python-testing              pytorch-patterns             rag-architect
research-ops                rules-distill                safety-guard
search-first                security-review              security-scan
skill-comply                spec-driven-workflow         sql-database-assistant
statistical-analyst         strategic-compact            tdd-workflow
tech-debt-tracker           verification-loop
```
