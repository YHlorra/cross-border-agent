# cross-border-agent

> 个人测试项目——内置约 2 万条亚马逊产品 mock 数据，主要用于测试中间件和智能体编排相关流程；不涉及真实生产环境，也未打通亚马逊或 1688 等平台。后续可在此基础上扩展接入各 ERP 和电商平台的 MCP 与 API。

[中文](#) · [English](README.en.md)

## 这是什么

本项目为个人测试项目，内置约 2 万条亚马逊产品 mock 数据，主要用于测试中间件和智能体编排相关流程。不涉及真实生产环境，也未打通亚马逊或 1688 等平台。后续可在此基础上扩展接入各 ERP 和电商平台的 MCP 与 API。

由三个 LangGraph agent 组成：

- **选品（Selection）**：用自然语言描述想查的品类，agent 基于内置 mock 语料做规则初筛 + 五维评分，输出带评分与理由的选品报告
- **Listing 生成（Listing Copy）**：给定一个选中的产品，agent 生成目标市场可发布的英文/中文 listing 文案（标题、五点描述、关键词）
- **通用对话（General Chat）**：日常查询走通用 agent；选品与 listing 是它的两个工具——由模型按用户意图自主决定何时调用

底层架构是 LangChain 1.x `create_agent` 构建 + LangGraph 运行时驱动的多智能体系统：通用 agent 是日常默认通道，`run_selection` / `run_listing` 是它的两个工具调用。

## Demo

工作台是 chat-first：所有能力都从输入框触发——`/` 唤起技能面板，自然语言直接对话。典型用法：

```
> /选品  空气炸锅 200 元内 厨房小家电

[ skill 注入：选品方法论 + run_selection 工具 ]
▶ 检索内置 mock 语料 ...
▶ 预筛 47 条候选 ...
▶ 5 维评分：销量 / 利润空间 / 竞品密度 / 物流 / 平台合规
✓ 报告：3 条推荐

  1. 某款圆形 3.5L 空气炸锅
     综合分 8.4 · 预估毛利 38% · 1688 价 ¥145 / 亚马逊售价 $39.99
     风险：评论 4.3 偏均值；FBA 配送到美国站可
     行动建议：先投 50 台试水，关注差评关键词
  2. ...

> 给 1 号起草一个 Amazon US listing

[ skill 注入：Listing 文案方法论 + run_listing 工具 ]
▶ 从语料中提取竞品 listing 前 10 名 ...
▶ 抽取卖点关键词 ...
✓ 草稿入列表（侧栏「列表」可继续编辑）

> 同样的产品生成一个 1688 中文版

[ skill 注入：平台规则 + Listing 文案方法论 ]
✓ 草稿入列表
```

侧栏的会话列表保留所有历史 session；点开任意 session 立即续聊（事件级回放，不重跑 LLM）。每个 session 可单独停止——发送按钮在运行中变形为停止线。

## 核心能力

### 多智能体编排
- **三个 LangGraph agent**：selection / listing / general，由 LangGraph 状态机驱动
- **工具即能力**：选品与 listing 不是固定流程，而是 agent 可调用的工具；模型自主决定何时调用
- **卡片事件渲染**：流式 NDJSON → 卡片事件 → 聊天 UI 实时呈现
- **可取消的流式链路**：用户中途可中止当前 run

### 真实 LLM 工具调用
- **原生结构化输出**：评分、理由、listing 字段都是 Pydantic 强类型
- **错误透传不降级**：模型/工具错误如实呈现，不静默退化
- **Thinking 摘要 + 工具进度**逐步流式呈现

### 数据层
- **PostgreSQL + pgvector**：约 2 万条亚马逊产品 mock 语料，规则初筛 + 向量检索
- **适配器模式**：`ProductDataAdapter` 解耦数据源（当前 PG-only）
- **持久化记忆**：memory graph 跨会话保留用户偏好与实体关系
- **平台规则硬约束**：Amazon 禁用的合规词与差评反链已硬接入 marketplaces.json → 触发即 BLOCKER，不静默改写

### 工程化
- **Hono BFF**：27 API 路由 + 静态托管 + 与 agent 进程的 HTTP 桥
- **DBOS durable execution**：节点级重试 / 恢复
- **Langfuse observability**：trace + token 用量 + 延迟全链路可见
- **Token/skill 化的方法论**：`src/agent/prompts/skills/` 把选品方法论、生图 SOP、Listing 文案、平台规则、差评反链封装成可复用 skill，按需加载（详见下方 [领域方法论 Skill 库](#领域方法论-skill-库) 节）

### 前端
- **Chat-first 工作台**：输入框是唯一主入口；`/` 唤起技能面板（`/选品` `/Listing` `/生图` `/差评` `/平台规则` `/压缩` `/新会话`），自然语言直接对话——技能是否调用、何时调用由模型自主决定
- **四个视图路由**：chat（默认）| config（模型与 provider 配置）| listings（草稿管理）| preferences（记忆/偏好 CRUD）——走路由，不嵌面板
- **多 session 并行**：Codex/Claude 风格——侧栏会话列表，各自后台运行，单独停止（运行中发送按钮变形为停止线）
- **NDJSON 真实流式**：thinking 摘要 + 工具进度 + 卡片事件逐步呈现
- **事件级回放**：点开历史 session 立即续聊，不重跑 LLM

## 技术栈

| 层 | 技术 |
|---|---|
| Agent（Python 3.12+） | LangGraph 1.x · LangChain 1.x (`create_agent`) · Pydantic v2 · aiohttp · arcships-aimux（多 provider 网关） |
| 持久化与向量 | PostgreSQL 16 + Drizzle ORM + pgvector |
| 可观测性 | Langfuse 2.x |
| Durable execution | DBOS |
| BFF（Node 24） | Hono · zod · @hono/node-server |
| 前端 | Vite 6 + React 19 + TypeScript + TanStack Query 5 + antd 5 + @ant-design/pro-components + ECharts + react-markdown |
| 运行时 | Python 3.12 / 3.13 · Node.js 24 · PostgreSQL 16（端口 5433） |

## 领域方法论 Skill 库

业务方法论不进系统提示词——**作为可装载 skill 独立维护**，按需注入上下文。这是本项目把"领域知识"工程化的核心做法：方法论可版本控制、可审计、可复用，提示词主体保持精简。

| Skill | 触发斜杠 | 一句话作用 |
|---|---|---|
| `ecom-selection-method` | `/选品` | 选品方法论战术手册：平台-语言契约（Amazon 英文 / 1688 中文）、工具分层（`run_selection` 确定性内核 vs `corpus_overview` 等灵活探测）、先探测后深入、关键词分侧迭代、不对称结果如实呈现 |
| `ecom-listing-copywriting` | `/Listing` | 亚马逊 Listing 文案方法论：标题公式、五点分工模板、关键词四级分配与覆盖率自检、`run_listing`/`save_listing` 分工、三风格变体、本地化思维、事实锁定纪律 |
| `ecom-listing-platform-rules` | `/平台规则` | 三平台（亚马逊 / TikTok Shop / Shopify）数字规则表、claim 合规禁令、站点语言匹配——与代码层 `marketplaces.json` 硬编码映射（trigger 即 BLOCKER，不静默改写） |
| `ecom-review-mining` | `/差评` | 差评→卖点反向链路：A/B 对照采集（已带该功能 vs 同品类头部不带）、信号阈值表、三维交叉验证、投诉到卖点转化规则 |
| `ecom-imagegen` | `/生图` | 商品图生图 SOP：六要素提示词框架（主体 / 场景 / 光影 / 构图 / 风格 / 文字，顺序固定）、平台渠道画幅与合规差异、生成后合规检查清单 |

### 三层装载模型

```
装配层（每次 run 全文注入）   选品方法论    ← 必需要素，模型不允许忘记
────────────────────────────────────────────────────────────
按需 load_skill（slash 触发    文案 · 平台规则 · 差评 · 生图
或模型自主判断）
────────────────────────────────────────────────────────────
运行时（始终在场）             工具定义 · 输出契约 · 错误码 · 模型身份
```

**与 UI 的关系**：工作台输入框打 `/` 唤起的技能面板就是这 5 个 skill + 2 个 UI 状态命令（`/压缩` `/新会话`）。`/选品` `/Listing` 等领域命令走 chat 主链路，触发方法论注入；`/压缩` `/新会话` 是前端本地命令，不调模型。

**工程价值**：方法论文件独立（`src/agent/prompts/skills/*.md`）意味着可以单独 diff / 评审 / 回滚，不会污染 agent 角色提示词；触发斜杠 + version 字段让"哪个方法论在用"可观测可回溯。

## 系统架构

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

## 快速开始

### 前置

- Node.js 24+ · Python 3.12 或 3.13 · PostgreSQL 16（端口 5433）
- LLM provider key（DeepSeek / OpenAI / Anthropic 等任意 aimux 兼容 provider）

### 启动

```bash
# 1) 拉依赖（仓库根）
npm install

# 2) 启动 PG（项目自带便携脚本）
scripts\pg_up.bat          # Windows
# 或：bash scripts/pg_up.sh

# 3) 写 .env.local
cp .env.local.example .env.local
# 编辑 .env.local，至少填 LLM_API_KEY

# 4) 三进程：workbench、bff、agent
npm run dev:workbench      # http://127.0.0.1:5173
npm run dev:bff            # http://127.0.0.1:3000
python -m agent.server     # http://127.0.0.1:8765
```

打开 <http://127.0.0.1:5173>，在输入框用自然语言描述想查的品类。

## 模型配置

通过 `.env.local` 配置：

```bash
LLM_PRIMARY_PROVIDER=deepseek
LLM_PRIMARY_MODEL=deepseek-chat
LLM_CHEAP_PROVIDER=deepseek
LLM_CHEAP_MODEL=deepseek-chat
LLM_API_KEY=sk-...
```

`PRIMARY` 用于 retrieve / quality_score / report 等关键节点；`CHEAP` 用于 hot_filter 这种大批量预筛节点——两套模型分离是控制单次 run token 成本的核心手段。

## 已知边界

- **必须自行提供 LLM_API_KEY**——本项目是真实模型驱动，不带 mock 兜底
- **语料规模约 2 万条亚马逊产品 mock 数据**——本地 PG，非真实平台数据；覆盖面取决于你的 ETL
- **未对接真实平台**——亚马逊 / 1688 / TikTok Shop 等电商平台均未打通，所有「货源价 / 竞品售价 / 平台规则」均来自内置 mock 语料或硬编码规则表；可作为后续接入各 ERP / 平台 MCP 与 API 的基线
- **当前覆盖**：选品 + listing 两条产品线；定价、广告、客服、订单等模块按阶段推进中
- **平台规则硬约束**：Amazon 禁用的合规词与差评反链已硬接入 marketplaces.json → 触发即 BLOCKER，不静默改写
- **不要把真实 `.env` / `providers.json` 提交到公开仓库**——`.env*` 与 `data/selection/providers.json` 已被 `.gitignore` 保护

> [!WARNING]
> **API Key 暴露风险**：本项目用真实模型跑真实选品，`.env.local` 里是付费 provider 的真 key。意外推送等于直接泄漏——务必确认 `.gitignore` 段生效再公开。

## AI 编程助手接入

本项目自带 `AGENTS.md`（项目工作流规则）+ `src/agent/prompts/skills/`（领域方法论 skill 库）。把本仓库路径加入 Cursor / Claude Code / Continue 等工具的工作区后即可自动加载：

- `AGENTS.md`：跨开发阶段的硬规则
- `src/agent/prompts/skills/`：选品方法论、生图 SOP、Listing 文案、平台规则、差评反链五类 skill

## 仓库结构

| 内容 | 位置 |
|---|---|
| 选品 / listing / 通用 agent | `src/agent/` |
| Hono BFF（27 路由） | `bff/` |
| 聊天工作台 + antd 管理控制台 | `workbench/` |
| 领域方法论 skill（5 个：`ecom-selection-method` / `ecom-listing-copywriting` / `ecom-listing-platform-rules` / `ecom-review-mining` / `ecom-imagegen`） | `src/agent/prompts/skills/` |
| 端到端 smoke 脚本（p11 治理 / p12 单聊 / p13 并行 / p14 右键 / p15 UI 刷新 / p16 排队草稿 / p17 chat 路由） | `npm run smoke:p{11..17}` |

## Star History

<a href="https://star-history.com/#YHlorra/cross-border-agent&Date">
  <img src="https://api.star-history.com/svg?repos=YHlorra/cross-border-agent&type=Date" alt="Star History Chart" />
</a>

## License

MIT

## 致谢

本项目建立在开源社区的肩膀之上。所用到的关键组件与灵感来源，按角色分组列出，向作者与维护者致以敬意。

### 核心运行时

- **LangGraph / LangChain**（LangChain 团队）—— LangGraph 1.x 状态机驱动整个 agent 编排；`create_agent` 是 LangChain 1.x 的入口
- **Pydantic**（Samuel Colvin 等）—— 结构化输出与配置校验的事实标准
- **PostgreSQL + pgvector**（PostgreSQL 全球开发组 / pgvector 维护者）—— 语料存储与向量检索
- **aiohttp**（aiohttp 团队）—— agent 进程内部异步 HTTP 客户端

### 工作台 / BFF

- **React + react-dom**（Meta Platforms 与社区）—— UI 框架
- **Vite**（Evan You 与 Vite 团队）—— 开发服务器与生产构建
- **Hono + @hono/node-server**（Yusuke Wada 与 Hono 团队）—— BFF 框架
- **TanStack Query**（Tanner Linsley 与 TanStack 团队）—— 服务端状态管理
- **Ant Design + @ant-design/pro-components**（蚂蚁集团与社区）—— 后台与管理面组件库
- **Appica UI**（[@appica-dev/appica-ui](https://github.com/appica-dev/appica-ui)）—— 前端 UI 组件库
- **ECharts**（Apache ECharts 团队）—— 报告图表
- **react-markdown + remark-gfm + rehype-highlight**—— Markdown 渲染与代码高亮

### 可观测性与持久化执行

- **Langfuse**（Langfuse 团队）—— LLM trace 与 token 用量观测
- **DBOS**（DBOS 团队）—— 节点级 durable execution

### 测试与开发工具

- **pytest + pytest-asyncio**—— Python 测试栈
- **Vitest**（Vitest 团队）—— workbench / bff 单测
- **Playwright**（Microsoft）—— E2E 冒烟脚本
- **TypeScript + tsx**—— 类型系统与开发态 Node 运行时
- **Tailwind CSS**（Tailwind Labs）—— 工作台样式底座

### 方法论借鉴

`src/agent/prompts/skills/` 下的 5 个领域方法论 skill（选品 / Listing 文案 / 平台规则 / 差评反链 / 生图）的部分写法与社区公开的电商 prompt 资料同源，特别感谢以下参考仓库的作者（仅做方法论学习，未复制其源代码）：

- [`listforge/prompts`](https://github.com/listforge/prompts) · [`nexscope/amazon-skills`](https://github.com/nexscope/amazon-skills) —— Listing 文案 / 平台规则方法论的主要参考
- [`openai/codex`](https://github.com/openai/codex) —— chat-first 工作台 / 多 session 并行 / 工具即能力等工程范式的对标
- [`upsidelab/enthusiast`](https://github.com/upsidelab/enthusiast) —— LangChain + 多 agent 编排思路的早期参照

如果本项目对您或您的项目有所帮助，欢迎 star 反馈。
