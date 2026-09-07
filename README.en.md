# cross-border-agent

> AI-driven product sourcing and listing tool for cross-border e-commerce. Combines "scan 1688 supply + scan Amazon competition + write localized copy" into one multi-agent pipeline.

[中文](README.md) · [English](#)

## What it is

cross-border-agent is a multi-agent workbench for individual cross-border sellers, composed of three LangGraph agents:

- **Selection** — describe the product category in natural language; the agent retrieves 1688 supply and Amazon competition data, runs rule pre-filtering + 5-dimension scoring, and outputs a sourcing report with scores and rationale
- **Listing copy** — given a selected product, the agent generates marketplace-ready English/Chinese listing copy (title, five bullets, keywords)
- **General chat** — day-to-day queries route through the general agent; selection and listing are its two tools, called by the model based on user intent

The runtime is LangChain 1.x `create_agent` orchestrated over a LangGraph state machine: the general agent is the default channel; `run_selection` and `run_listing` are its two tool calls.

## Demo

The workbench is chat-first: every capability is invoked from the input box — `/` opens the skill palette, natural language goes straight to the model. A typical session:

```
> /选品  空气炸锅 200 元内 厨房小家电

[ skill injected: selection methodology + run_selection tool ]
▶ retrieving 1688 supply ...
▶ pre-filtering 47 candidates ...
▶ scoring on 5 dimensions: sales / margin / competition density / logistics / platform compliance
✓ report: 3 recommendations

  1. Round 3.5L air fryer, model X
     overall 8.4 · est. margin 38% · 1688 price ¥145 / Amazon $39.99
     risk: rating 4.3 (slightly below average); FBA to US feasible
     action: pilot 50 units; watch negative review keywords
  2. ...

> draft an Amazon US listing for item 1

[ skill injected: listing copy methodology + run_listing tool ]
▶ scraping top 10 competitor listings ...
▶ extracting selling-point keywords ...
✓ draft saved to listings (edit further in the sidebar `listings` route)

> same product, generate a 1688 Chinese version

[ skill injected: platform rules + listing copy methodology ]
✓ draft saved to listings
```

The sidebar keeps every session; clicking one replays the event stream and resumes the conversation (no LLM rerun). Each session can be stopped independently — the send button morphs into a stop bar while a run is in flight.

## Core capabilities

### Multi-agent orchestration
- **Three LangGraph agents** (selection / listing / general) driven by a LangGraph state machine
- **Tools as capabilities** — selection and listing are not fixed flows but model-callable tools; the model decides when to invoke them
- **Card-event rendering** — streaming NDJSON → card events → real-time chat UI
- **Cancellable streaming** — users can abort a run mid-flight

### Real LLM tool calling
- **Native structured output** — scores, rationale, and listing fields are Pydantic-strong typed
- **Error pass-through** — model/tool errors surface as-is, no silent degradation
- **Streaming thinking summary + tool progress**

### Data layer
- **PostgreSQL + pgvector** — 28k product corpus, rule pre-filter + vector retrieval
- **Adapter pattern** — `ProductDataAdapter` decouples data sources (currently PG-only)
- **Persistent memory** — memory graph preserves user preferences and entity relationships across sessions
- **Hard platform rules** — Amazon compliance terms and review-deflection links are baked into `marketplaces.json`; triggers become BLOCKERs, never silent rewrites

### Engineering
- **Hono BFF** — 27 API routes + static hosting + HTTP bridge to the agent process
- **DBOS durable execution** — per-node retry and recovery
- **Langfuse observability** — full tracing + token usage + latency
- **Tokenized methodology** — `src/agent/prompts/skills/` packages sourcing methodology, image generation SOP, listing copy, platform rules, and review deflection as reusable skills, loaded on demand (see the [Methodology skills](#methodology-skills) section below)

### Frontend
- **Chat-first workbench** — the input box is the only primary surface; `/` opens a skill palette (`/选品` `/Listing` `/生图` `/差评` `/平台规则` `/压缩` `/新会话`), natural language goes straight to the model — the model decides when and whether to invoke skills
- **Four routed views** — chat (default) | config (model & provider) | listings (draft management) | preferences (memory CRUD) — routes, not docked panels
- **Parallel multi-session** — Codex/Claude style: sidebar session list, each session runs in its own background; stop a single one without affecting others (the send button morphs into a stop bar while running)
- **NDJSON real streaming** — thinking summary + tool progress + card events
- **Event-level replay** — opening a past session resumes from the event stream, no LLM rerun

## Tech stack

| Layer | Technology |
|---|---|
| Agent (Python 3.12+) | LangGraph 1.x · LangChain 1.x (`create_agent`) · Pydantic v2 · aiohttp · arcships-aimux (multi-provider gateway) |
| Persistence & vector | PostgreSQL 16 + Drizzle ORM + pgvector |
| Observability | Langfuse 2.x |
| Durable execution | DBOS |
| BFF (Node 24) | Hono · zod · @hono/node-server |
| Frontend | Vite 6 + React 19 + TypeScript + TanStack Query 5 + antd 5 + @ant-design/pro-components + ECharts + react-markdown |
| Runtime | Python 3.12 / 3.13 · Node.js 24 · PostgreSQL 16 (port 5433) |

## Methodology skills

Business methodology does not live in the system prompt — it lives as **independently-loadable skills**, injected on demand. This is the project's core approach to operationalizing domain knowledge: methodology is version-controlled, auditable, and reusable, and the agent's role prompt stays lean.

| Skill | Slash trigger | What it does |
|---|---|---|
| `ecom-selection-method` | `/选品` | Sourcing methodology playbook: platform-language contract (Amazon English / 1688 Chinese), tool layering (`run_selection` as the deterministic core vs `corpus_overview` etc. as flexible probes), probe-before-deepen, side-by-side keyword iteration, asymmetric results stated honestly |
| `ecom-listing-copywriting` | `/Listing` | Amazon listing copy methodology: title formula, five-bullet template, four-tier keyword allocation with coverage self-check, `run_listing` / `save_listing` split, three-style variants, localization mindset, fact-locking discipline |
| `ecom-listing-platform-rules` | `/平台规则` | Three-platform (Amazon / TikTok Shop / Shopify) numeric rules table, claim compliance prohibitions, site-language matching — hardcoded mapped to `marketplaces.json` in code (trigger = BLOCKER, never silent rewrite) |
| `ecom-review-mining` | `/差评` | Review→selling-point reverse link: A/B paired collection (products with the feature vs. category leaders without), signal threshold table, three-dimensional cross-validation, complaint-to-feature transformation rules |
| `ecom-imagegen` | `/生图` | Product image-generation SOP: six-element prompt framework (subject / scene / light / composition / style / text, fixed order), platform-channel canvas & compliance differences, post-generation compliance checklist |

### Three-layer loading model

```
Assembly (full-text inject every run)   selection methodology   ← essential; model may not forget
─────────────────────────────────────────────────────────────────
On-demand load_skill (slash or model    copy · platform rules · review-mining · image
judgment)
─────────────────────────────────────────────────────────────────
Runtime (always present)                tool defs · output contracts · error codes · model identity
```

**UI binding**: the `/` palette in the workbench input surfaces exactly these 5 skills + 2 UI-state commands (`/压缩` `/新会话`). Domain commands (`/选品` `/Listing` etc.) route through the chat main loop and trigger methodology injection; `/压缩` and `/新会话` are local frontend commands that don't call the model.

**Engineering value**: methodology files are independent (`src/agent/prompts/skills/*.md`), so they can be diffed / reviewed / rolled back separately without polluting the agent's role prompt. The slash trigger plus the `version:` frontmatter field make "which methodology is in use" observable and traceable.

## Architecture

```
┌────────────┐   NDJSON  ┌─────────┐   HTTP   ┌─────────────┐
│ Workbench  │ ────────▶ │  BFF    │ ────────▶│  Agent      │
│ (Vite+     │ ◀──────── │ (Hono,  │ ◀─────── │  (LangGraph │
│  React)    │   SSE     │  :3000) │   stream │  :8765)     │
└────────────┘           └─────────┘          └─────────────┘
                                                    │
                                                    ▼
                                            ┌──────────────┐
                                            │  PostgreSQL  │
                                            │  + pgvector  │
                                            │  :5433       │
                                            └──────────────┘
```

## Quick start

### Prerequisites

- Node.js 24+ · Python 3.12 or 3.13 · PostgreSQL 16 (port 5433)
- An LLM provider key (DeepSeek / OpenAI / Anthropic — any aimux-compatible provider)

### Run

```bash
# 1) Install dependencies (repo root)
npm install

# 2) Start PG (project ships portable scripts)
scripts\pg_up.bat          # Windows
# or:  bash scripts/pg_up.sh

# 3) Create .env.local
cp .env.local.example .env.local
# Edit .env.local, at minimum set LLM_API_KEY

# 4) Three processes: workbench, bff, agent
npm run dev:workbench      # http://127.0.0.1:5173
npm run dev:bff            # http://127.0.0.1:3000
python -m agent.server     # http://127.0.0.1:8765
```

Open <http://127.0.0.1:5173> and describe the category you want to source.

## Model configuration

Configure via `.env.local`:

```bash
LLM_PRIMARY_PROVIDER=deepseek
LLM_PRIMARY_MODEL=deepseek-chat
LLM_CHEAP_PROVIDER=deepseek
LLM_CHEAP_MODEL=deepseek-chat
LLM_API_KEY=sk-...
```

`PRIMARY` powers the retrieve / quality_score / report nodes; `CHEAP` powers high-volume pre-filter nodes like `hot_filter`. Splitting the two is the main lever for per-run token cost.

## Known limits

- **You must supply your own `LLM_API_KEY`** — the project runs against real models; no mock fallback
- **28k product corpus** — local PG, not real-time scraping; coverage depends on your ETL
- **Current scope** — sourcing + listing only; pricing, ads, support, orders are upcoming phases
- **Hard platform rules** — Amazon compliance terms and review-deflection links in `marketplaces.json` trigger BLOCKERs, never silent rewrites
- **Do not commit real `.env` / `providers.json`** — both are covered by `.gitignore`

> [!WARNING]
> **API key exposure risk**: this project calls paid providers for real. Your `.env.local` holds a live key. Accidental push = direct leak. Confirm `.gitignore` rules before going public.

## AI coding agent setup

The project ships with `AGENTS.md` (project workflow rules) and `src/agent/prompts/skills/` (domain methodology skills). Adding the repo path to Cursor / Claude Code / Continue automatically loads:

- `AGENTS.md` — cross-phase hard rules
- `src/agent/prompts/skills/` — five skills: sourcing methodology, image SOP, listing copy, platform rules, review deflection

## Repo layout

| Content | Location |
|---|---|
| Selection / listing / general agent | `src/agent/` |
| Hono BFF (27 routes) | `bff/` |
| Chat workbench + antd admin console | `workbench/` |
| Domain methodology skills (5: `ecom-selection-method` / `ecom-listing-copywriting` / `ecom-listing-platform-rules` / `ecom-review-mining` / `ecom-imagegen`) | `src/agent/prompts/skills/` |
| End-to-end smoke scripts (p11 governance / p12 single chat / p13 parallel / p14 context menu / p15 UI refresh / p16 queued drafts / p17 chat routing) | `npm run smoke:p{11..17}` |

## Star History

<a href="https://star-history.com/#YHlorra/cross-border-agent&Date">
  <img src="https://api.star-history.com/svg?repos=YHlorra/cross-border-agent&type=Date" alt="Star History Chart" />
</a>

## License

MIT
