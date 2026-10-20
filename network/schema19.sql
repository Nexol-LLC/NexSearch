-- NexSearch 1.6.4. Keep dedupe keys when a user clears notification history.
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS inn text NOT NULL DEFAULT '';
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
CREATE INDEX IF NOT EXISTS tasks_open_due_owner ON followup_tasks(due_at,employee_id) WHERE status='open';
CREATE INDEX IF NOT EXISTS notifications_visible_owner ON notifications(employee_id,created_at DESC) WHERE deleted_at IS NULL;
INSERT INTO schema_meta(version) VALUES(19) ON CONFLICT DO NOTHING;
