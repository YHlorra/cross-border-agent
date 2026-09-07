/**
 * Schema 真相源— 镜像 src/agent/persistence/store.py init_db 的
 * SQLite 列(逐列对照 2026-09-06 全量调查),列类型保持 TEXT 以延续 ISO-8601
 * 字符串比较语义;向量列进 preference_embeddings(pgvector 替代 sqlite-vec)。
 *
 * 注意:EMBEDDING_DIM 在 generate 时内插;改维度需新迁移。
 */
import { sql } from "drizzle-orm";
import {
  doublePrecision,
  integer,
  pgTable,
  text,
  index,
  uniqueIndex,
  vector,
} from "drizzle-orm/pg-core";

const EMBEDDING_DIM = Number(process.env.EMBEDDING_DIM ?? 1536);

export const selectionRuns = pgTable("selection_runs", {
  id: text("id").primaryKey(),
  query: text("query").notNull(),
  seedKeyword: text("seed_keyword"),
  decision: text("decision"),
  reportJson: text("report_json"),
  createdAt: text("created_at").notNull(),
});

export const listingRuns = pgTable("listing_runs", {
  id: text("id").primaryKey(),
  sessionId: text("session_id"),
  productId: text("product_id"),
  marketCode: text("market_code"),
  listingJson: text("listing_json").notNull(),
  title: text("title"),
  createdAt: text("created_at").notNull(),
});

export const messages = pgTable(
  "messages",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id").notNull(),
    runId: text("run_id").notNull(),
    eventType: text("event_type").notNull(),
    sequence: integer("sequence").notNull(),
    payload: text("payload").notNull(),
    answerFailed: integer("answer_failed").default(0),
    createdAt: text("created_at").notNull(),
  },
  (t) => [
    uniqueIndex("uq_messages_session_run_seq").on(t.sessionId, t.runId, t.sequence),
    index("idx_messages_session").on(t.sessionId, t.sequence),
    index("idx_messages_run").on(t.runId),
    index("idx_messages_event_type").on(t.eventType),
  ],
);

export const entityEdges = pgTable(
  "entity_edges",
  {
    id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
    srcType: text("src_type").notNull(),
    srcId: text("src_id").notNull(),
    rel: text("rel").notNull(),
    dstType: text("dst_type").notNull(),
    dstId: text("dst_id").notNull(),
    validFrom: text("valid_from").notNull(),
    validUntil: text("valid_until"),
    runId: text("run_id"),
    createdAt: text("created_at").notNull(),
  },
  (t) => [
    index("idx_entity_edges_src").on(t.srcType, t.srcId, t.rel),
    index("idx_entity_edges_dst").on(t.dstType, t.dstId),
  ],
);

export const preferenceEmbeddings = pgTable(
  "preference_embeddings",
  {
    id: integer("id").primaryKey().generatedAlwaysAsIdentity(),
    category: text("category").notNull(),
    preferenceText: text("preference_text").notNull(),
    sourceRunId: text("source_run_id").notNull(),
    sourceSessionId: text("source_session_id").notNull(),
    createdAt: text("created_at").notNull(),
    lastReinforcedAt: text("last_reinforced_at").notNull(),
    reinforcementCount: integer("reinforcement_count").default(1),
    expiresAt: text("expires_at"),
    supersededBy: integer("superseded_by"),
    active: integer("active").default(1),
    schemaVersion: integer("schema_version").default(1),
    // pgvector 列(change 2)— SQLite 模式的 preference_vec 由本列取代。
    // 维度内插:EMBEDDING_DIM(缺省 1536)。
    embedding: vector("embedding", { dimensions: EMBEDDING_DIM }),
  },
  (t) => [
    index("idx_pref_category_active")
      .on(t.category)
      .where(sql`active = 1`),
    index("idx_pref_session").on(t.sourceSessionId),
    index("idx_pref_created").on(t.createdAt),
    index("idx_pref_vec").using(
      "hnsw",
      t.embedding.op("vector_l2_ops"),
    ),
  ],
);

export const sessionMeta = pgTable("session_meta", {
  sessionId: text("session_id").primaryKey(),
  title: text("title"),
  pinned: integer("pinned").default(0),
  lastReadAt: text("last_read_at"),
  updatedAt: text("updated_at").notNull(),
});

export const approvalRequests = pgTable(
  "approval_requests",
  {
    runId: text("run_id").primaryKey(),
    sessionId: text("session_id").notNull(),
    kind: text("kind").notNull(),
    summary: text("summary"),
    status: text("status").notNull().default("pending"),
    decision: text("decision"),
    reason: text("reason"),
    createdAt: text("created_at").notNull(),
    resolvedAt: text("resolved_at"),
  },
  (t) => [index("idx_approvals_status").on(t.status, t.createdAt)],
);

// ── 语料库——亚马逊真实抓取数据集,选品 mock 数据源 ──────────────────
// 源:data/corpus/corpus.db(build_db.py 构建,raw 40,940 items 按 ASIN 去重)。
// 字段三分类:商品本体 / 市场信号 / 溯源;行数精确断言见 scripts/load_corpus.py。

export const corpusProducts = pgTable(
  "corpus_products",
  {
    // 商品本体
    asin: text("asin").primaryKey(),
    title: text("title").notNull(),
    img: text("img"),
    // 市场信号(数值类型:价差计算/排序需要)
    priceUsd: doublePrecision("price_usd"),
    rating: doublePrecision("rating"),
    reviewCount: integer("review_count"),
    // 溯源
    categoryQuery: text("category_query").notNull(),
    majorCategory: text("major_category").notNull(),
    sourcePos: integer("source_pos"),
    scrapedAt: text("scraped_at"),
  },
  (t) => [
    index("idx_corpus_major").on(t.majorCategory),
    index("idx_corpus_query").on(t.categoryQuery),
  ],
);

/** 采集统计(非商品):每搜索词一条,用于品类厚度评估/补采决策,不进演示主链。 */
export const corpusCategories = pgTable("corpus_categories", {
  query: text("query").primaryKey(),
  major: text("major").notNull(),
  itemsScraped: integer("items_scraped"),
  distinctItems: integer("distinct_items"),
  scrapedAt: text("scraped_at"),
});

/** 构建元数据(出厂标签):built_at/source/distinct_products/price_coverage/边界声明。 */
export const corpusMeta = pgTable("corpus_meta", {
  key: text("key").primaryKey(),
  value: text("value"),
});
