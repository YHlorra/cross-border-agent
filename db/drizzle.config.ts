import "dotenv/config";
import { defineConfig } from "drizzle-kit";

// Drizzle 独占 schema 与迁移;DATABASE_URL 是运行时/迁移的唯一开关。
export default defineConfig({
  dialect: "postgresql",
  schema: "./schema.ts",
  out: "./migrations",
  dbCredentials: {
    url:
      process.env.DATABASE_URL ??
      "postgresql://postgres@127.0.0.1:5433/crossborder",
  },
  // EMBEDDING_DIM 决定 preference_embeddings.embedding 的 vector 维度
  // (drizzle-kit generate 时内插进迁移 SQL;改维度 = 重置该列迁移)。
  verbose: true,
});
