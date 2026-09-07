CREATE TABLE "corpus_categories" (
	"query" text PRIMARY KEY NOT NULL,
	"major" text NOT NULL,
	"items_scraped" integer,
	"distinct_items" integer,
	"scraped_at" text
);
--> statement-breakpoint
CREATE TABLE "corpus_meta" (
	"key" text PRIMARY KEY NOT NULL,
	"value" text
);
--> statement-breakpoint
CREATE TABLE "corpus_products" (
	"asin" text PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"img" text,
	"price_usd" double precision,
	"rating" double precision,
	"review_count" integer,
	"category_query" text NOT NULL,
	"major_category" text NOT NULL,
	"source_pos" integer,
	"scraped_at" text
);
--> statement-breakpoint
CREATE INDEX "idx_corpus_major" ON "corpus_products" USING btree ("major_category");--> statement-breakpoint
CREATE INDEX "idx_corpus_query" ON "corpus_products" USING btree ("category_query");