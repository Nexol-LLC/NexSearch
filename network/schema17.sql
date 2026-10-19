-- NexSearch 1.6.1: mailbox preferences and rich message cache; no data removal.
ALTER TABLE mail_accounts ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}';
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS body_html text NOT NULL DEFAULT '';
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS content_version integer NOT NULL DEFAULT 0;
INSERT INTO schema_meta(version) VALUES(17) ON CONFLICT DO NOTHING;
