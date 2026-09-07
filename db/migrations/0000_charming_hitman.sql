CREATE TABLE "entity_edges" (
	"id" integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "entity_edges_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1),
	"src_type" text NOT NULL,
	"src_id" text NOT NULL,
	"rel" text NOT NULL,
	"dst_type" text NOT NULL,
	"dst_id" text NOT NULL,
	"valid_from" text NOT NULL,
	"valid_until" text,
	"run_id" text,
	"created_at" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "listing_runs" (
	"id" text PRIMARY KEY NOT NULL,
	"session_id" text,
	"product_id" text,
	"market_code" text,
	"listing_json" text NOT NULL,
	"title" text,
	"created_at" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "messages" (
	"id" text PRIMARY KEY NOT NULL,
	"session_id" text NOT NULL,
	"run_id" text NOT NULL,
	"event_type" text NOT NULL,
	"sequence" integer NOT NULL,
	"payload" text NOT NULL,
	"answer_failed" integer DEFAULT 0,
	"created_at" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "preference_embeddings" (
	"id" integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "preference_embeddings_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1),
	"category" text NOT NULL,
	"preference_text" text NOT NULL,
	"source_run_id" text NOT NULL,
	"source_session_id" text NOT NULL,
	"created_at" text NOT NULL,
	"last_reinforced_at" text NOT NULL,
	"reinforcement_count" integer DEFAULT 1,
	"expires_at" text,
	"superseded_by" integer,
	"active" integer DEFAULT 1,
	"schema_version" integer DEFAULT 1,
	"embedding" vector(1536)
);
--> statement-breakpoint
CREATE TABLE "selection_runs" (
	"id" text PRIMARY KEY NOT NULL,
	"query" text NOT NULL,
	"seed_keyword" text,
	"decision" text,
	"report_json" text,
	"created_at" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "session_meta" (
	"session_id" text PRIMARY KEY NOT NULL,
	"title" text,
	"pinned" integer DEFAULT 0,
	"archived" integer DEFAULT 0,
	"last_read_at" text,
	"updated_at" text NOT NULL
);
--> statement-breakpoint
CREATE INDEX "idx_entity_edges_src" ON "entity_edges" USING btree ("src_type","src_id","rel");--> statement-breakpoint
CREATE INDEX "idx_entity_edges_dst" ON "entity_edges" USING btree ("dst_type","dst_id");--> statement-breakpoint
CREATE UNIQUE INDEX "uq_messages_session_run_seq" ON "messages" USING btree ("session_id","run_id","sequence");--> statement-breakpoint
CREATE INDEX "idx_messages_session" ON "messages" USING btree ("session_id","sequence");--> statement-breakpoint
CREATE INDEX "idx_messages_run" ON "messages" USING btree ("run_id");--> statement-breakpoint
CREATE INDEX "idx_messages_event_type" ON "messages" USING btree ("event_type");--> statement-breakpoint
CREATE INDEX "idx_pref_category_active" ON "preference_embeddings" USING btree ("category") WHERE active = 1;--> statement-breakpoint
CREATE INDEX "idx_pref_session" ON "preference_embeddings" USING btree ("source_session_id");--> statement-breakpoint
CREATE INDEX "idx_pref_created" ON "preference_embeddings" USING btree ("created_at");--> statement-breakpoint
CREATE INDEX "idx_pref_vec" ON "preference_embeddings" USING hnsw ("embedding" vector_l2_ops);