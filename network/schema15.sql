ALTER TABLE carriers ADD COLUMN IF NOT EXISTS phone text NOT NULL DEFAULT '';
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS email text NOT NULL DEFAULT '';
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS work_terms text NOT NULL DEFAULT '';
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS vat_mode text NOT NULL DEFAULT 'unknown';
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS vat_rate numeric;
CREATE TABLE IF NOT EXISTS search_schedules (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),run_at timestamptz NOT NULL,
 target integer NOT NULL CHECK(target BETWEEN 1 AND 50),criteria jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','queued','cancelled')),
 job_id uuid REFERENCES jobs(id),created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS schedules_due ON search_schedules(run_at) WHERE status='pending';
ALTER TABLE search_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_schedules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS schedules_scope ON search_schedules;
CREATE POLICY schedules_scope ON search_schedules USING(employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true') WITH CHECK(employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true');
INSERT INTO schema_meta(version) VALUES(15) ON CONFLICT DO NOTHING;
