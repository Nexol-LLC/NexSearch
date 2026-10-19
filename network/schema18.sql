-- NexSearch 1.6.3: reply metadata. Existing mail and credentials are preserved.
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS in_reply_to text NOT NULL DEFAULT '';
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS reference_ids text[] NOT NULL DEFAULT '{}';
INSERT INTO schema_meta(version) VALUES(18) ON CONFLICT DO NOTHING;
