# cross-border-agent

> Personal testing project — built-in ~20k Amazon product mock data, primarily used for exercising middleware and multi-agent orchestration flows. Not a production environment; no real Amazon/1688 integration. Intended as a baseline that can later be extended with ERP and marketplace MCP / API surfaces.

[中文](README.md) · [English](#)

## What it is

This is a personal testing project — built-in ~20k Amazon product mock data, used for testing middleware and agent orchestration flows. It does not run against a production environment, nor is it wired up to real Amazon or 1688 platforms. The codebase is intended as a baseline that can later be extended to connect ERP systems and marketplace MCP / API surfaces.

Composed of three LangGraph agents:

- **Selection** — describe the product category in natural language; the agent runs rule pre-filtering + 5-dimension scoring against the built-in mock corpus and outputs a sourcing report with scores and rationale
- **Listing copy** — given a selected product, the agent generates marketplace-ready English/Chinese listing copy (title, five bullets, keywords)
- **General chat** — day-to-day queries route through the general agent; selection and listing are its two tools, called by the model based on user intent

The runtime is LangChain 1.x `create_agent` orchestrated over a LangGraph state machine: the general agent is the default channel; `run_selection` and `run_listing` are its two tool calls.

## Demo

The workbench is chat-first: every capability is invoked from the input box — `/` opens the skill palette, natural language goes straight to the model. A typical session:

```
> /选品  空气炸锅 200 元内 厨房小家电

[ skill injected: selection methodology + run_selection tool ]
▶ querying the built-in mock corpus ...
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
▶ pulling top 10 competitor listings from the corpus ...
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
- **PostgreSQL + pgvector** — ~20k Amazon product mock corpus, rule pre-filter + vector retrieval
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
- **~20k Amazon product mock corpus** — local PG, not real platform data; coverage depends on your ETL
- **No real-platform integration** — Amazon / 1688 / TikTok Shop etc. are not wired up; every "supply price / competitor price / platform rule" comes from the built-in mock corpus or a hardcoded rule table. The codebase is intended as a baseline for future ERP / marketplace MCP and API extensions
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

## Acknowledgments

This project stands on the shoulders of the open-source community. The key components and inspirations used here are listed below by role — sincere thanks to the authors and maintainers.

### Core runtime

- **LangGraph / LangChain** (LangChain team) — LangGraph 1.x state machine drives the entire agent orchestration; `create_agent` is the entry point of LangChain 1.x
- **Pydantic** (Samuel Colvin et al.) — the de facto standard for structured outputs and config validation
- **PostgreSQL + pgvector** (PostgreSQL global development group / pgvector maintainers) — corpus storage and vector retrieval
- **aiohttp** (aiohttp team) — async HTTP client inside the agent process

### Workbench / BFF

- **React + react-dom** (Meta Platforms and the community) — UI framework
- **Vite** (Evan You and the Vite team) — dev server and production build
- **Hono + @hono/node-server** (Yusuke Wada and the Hono team) — BFF framework
- **TanStack Query** (Tanner Linsley and the TanStack team) — server-state management
- **Ant Design + @ant-design/pro-components** (Ant Group and the community) — admin and console components
- **Appica UI** ([@appica-dev/appica-ui](https://github.com/appica-dev/appica-ui)) — frontend UI component library
- **ECharts** (Apache ECharts team) — report charts
- **react-markdown + remark-gfm + rehype-highlight** — Markdown rendering and code highlighting

### Observability and durable execution

- **Langfuse** (Langfuse team) — LLM tracing and token-usage observability
- **DBOS** (DBOS team) — per-node durable execution

### Testing and developer tooling

- **pytest + pytest-asyncio** — Python test stack
- **Vitest** (Vitest team) — workbench / bff unit tests
- **Playwright** (Microsoft) — E2E smoke scripts
- **TypeScript + tsx** — type system and dev-time Node runtime
- **Tailwind CSS** (Tailwind Labs) — workbench styling base

### Methodology inspirations

The five domain-methodology skills under `src/agent/prompts/skills/` (selection / listing copy / platform rules / review mining / image generation) draw on publicly shared e-commerce prompt resources. Special thanks to the authors of the following reference repositories (methodology study only — no source code is copied):

- [`listforge/prompts`](https://github.com/listforge/prompts) · [`nexscope/amazon-skills`](https://github.com/nexscope/amazon-skills) — primary references for listing copy / platform-rule methodology
- [`openai/codex`](https://github.com/openai/codex) — the engineering benchmark for the chat-first workbench, parallel multi-session, and "tools as capabilities" patterns
- [`upsidelab/enthusiast`](https://github.com/upsidelab/enthusiast) — early reference for LangChain + multi-agent orchestration thinking

If this project is useful to you or your work, a star would be the most appreciated feedback.
