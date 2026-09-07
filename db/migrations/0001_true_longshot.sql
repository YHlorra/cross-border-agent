CREATE TABLE "approval_requests" (
	"run_id" text PRIMARY KEY NOT NULL,
	"session_id" text NOT NULL,
	"kind" text NOT NULL,
	"summary" text,
	"status" text DEFAULT 'pending' NOT NULL,
	"decision" text,
	"reason" text,
	"created_at" text NOT NULL,
	"resolved_at" text
);
--> statement-breakpoint
CREATE INDEX "idx_approvals_status" ON "approval_requests" USING btree ("status","created_at");