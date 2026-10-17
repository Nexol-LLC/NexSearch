-- 1.6 adds personal mail, durable notifications and delegated tasks.
ALTER TABLE followup_tasks ADD COLUMN IF NOT EXISTS assigned_by uuid REFERENCES employees(id);
UPDATE followup_tasks SET assigned_by=employee_id WHERE assigned_by IS NULL;
CREATE OR REPLACE FUNCTION nexsearch_can_assign(owner uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT owner::text=current_setting('nexol.employee_id',true)
 OR current_setting('nexol.is_admin',true)='true'
 OR EXISTS (SELECT 1 FROM employees e WHERE e.id=owner AND e.active AND e.approved AND (
   current_setting('nexol.role',true)='director'
   OR (current_setting('nexol.role',true)='manager' AND e.role='logist' AND e.department=current_setting('nexol.department',true))))
$$;
DROP POLICY IF EXISTS scope_write ON followup_tasks;
CREATE POLICY scope_write ON followup_tasks FOR ALL USING (nexsearch_can_assign(employee_id)) WITH CHECK (nexsearch_can_assign(employee_id));

CREATE TABLE IF NOT EXISTS notifications (
 id uuid PRIMARY KEY, employee_id uuid NOT NULL REFERENCES employees(id),
 kind text NOT NULL, title text NOT NULL, description text NOT NULL DEFAULT '',
 entity_id text NOT NULL DEFAULT '', dedupe_key text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), read_at timestamptz,
 UNIQUE(employee_id,dedupe_key));
CREATE INDEX IF NOT EXISTS notifications_owner_date ON notifications(employee_id,created_at DESC);
CREATE INDEX IF NOT EXISTS notifications_unread ON notifications(employee_id) WHERE read_at IS NULL;

CREATE TABLE IF NOT EXISTS mail_accounts (
 id uuid PRIMARY KEY, employee_id uuid NOT NULL REFERENCES employees(id),
 email text NOT NULL, login text NOT NULL, label text NOT NULL DEFAULT '',
 imap_host text NOT NULL, imap_port integer NOT NULL DEFAULT 993,
 smtp_host text NOT NULL DEFAULT '', smtp_port integer NOT NULL DEFAULT 465,
 secret bytea NOT NULL, enabled boolean NOT NULL DEFAULT true,
 folders jsonb NOT NULL DEFAULT '[]', sync_state jsonb NOT NULL DEFAULT '{}',
 last_sync_at timestamptz, last_error text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(employee_id,email));
CREATE TABLE IF NOT EXISTS mail_messages (
 id uuid PRIMARY KEY, employee_id uuid NOT NULL REFERENCES employees(id),
 account_id uuid NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,
 folder text NOT NULL, uidvalidity text NOT NULL, uid bigint NOT NULL,
 message_id text NOT NULL DEFAULT '', subject text NOT NULL DEFAULT '',
 sender text NOT NULL DEFAULT '', recipients jsonb NOT NULL DEFAULT '[]',
 addresses text[] NOT NULL DEFAULT '{}', sent_at timestamptz,
 seen boolean NOT NULL DEFAULT false, size_bytes bigint NOT NULL DEFAULT 0,
 body_text text, attachments jsonb NOT NULL DEFAULT '[]',
 cached_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(account_id,folder,uidvalidity,uid));
CREATE INDEX IF NOT EXISTS mail_messages_owner_date ON mail_messages(employee_id,sent_at DESC);
CREATE INDEX IF NOT EXISTS mail_addresses ON mail_messages USING gin(addresses);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['notifications','mail_accounts','mail_messages'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('DROP POLICY IF EXISTS personal_scope ON %I',tab);
  EXECUTE format('CREATE POLICY personal_scope ON %I USING (employee_id::text=current_setting(''nexol.employee_id'',true) OR current_setting(''nexol.is_admin'',true)=''true'') WITH CHECK (employee_id::text=current_setting(''nexol.employee_id'',true) OR current_setting(''nexol.is_admin'',true)=''true'')',tab);
 END LOOP;
END $$;
INSERT INTO schema_meta(version) VALUES(16) ON CONFLICT DO NOTHING;
