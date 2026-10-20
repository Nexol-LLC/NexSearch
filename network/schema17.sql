-- NexSearch 1.6.1: mailbox preferences and rich message cache; no data removal.
ALTER TABLE mail_accounts ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}';
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS body_html text NOT NULL DEFAULT '';
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS content_version integer NOT NULL DEFAULT 0;
INSERT INTO schema_meta(version) VALUES(17) ON CONFLICT DO NOTHING;
-- Also executed by the already installed 1.6.2 privileged updater.
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS in_reply_to text NOT NULL DEFAULT '';
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS reference_ids text[] NOT NULL DEFAULT '{}';
INSERT INTO schema_meta(version) VALUES(18) ON CONFLICT DO NOTHING;

-- Compatibility with installed privileged updaters.
-- NexSearch 1.6.4. Keep dedupe keys when a user clears notification history.
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS inn text NOT NULL DEFAULT '';
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
CREATE INDEX IF NOT EXISTS tasks_open_due_owner ON followup_tasks(due_at,employee_id) WHERE status='open';
CREATE INDEX IF NOT EXISTS notifications_visible_owner ON notifications(employee_id,created_at DESC) WHERE deleted_at IS NULL;
INSERT INTO schema_meta(version) VALUES(19) ON CONFLICT DO NOTHING;
