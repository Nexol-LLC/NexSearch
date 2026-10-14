-- Additive migration: keep company identities, role isolation and 1.3 records.
ALTER TABLE employees ADD COLUMN IF NOT EXISTS approved boolean NOT NULL DEFAULT false;
UPDATE employees SET approved=true WHERE is_admin;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS scanned integer NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS verified integer NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_progress jsonb NOT NULL DEFAULT '{}';
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS vehicles jsonb NOT NULL DEFAULT '[]';
ALTER TABLE carriers ADD COLUMN IF NOT EXISTS regions text NOT NULL DEFAULT '';
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS as_of timestamptz;
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS checked_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS valid_until timestamptz;
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS source_url text NOT NULL DEFAULT '';
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS date_precision text NOT NULL DEFAULT 'timestamp';
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS vat_rate numeric;
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS pricing_basis text NOT NULL DEFAULT 'trip' CHECK(pricing_basis IN ('trip','km'));
UPDATE route_quotes SET as_of=created_at,date_precision='unknown' WHERE as_of IS NULL;
ALTER TABLE route_quotes ALTER COLUMN as_of SET NOT NULL;
ALTER TABLE route_quotes ALTER COLUMN as_of SET DEFAULT now();
ALTER TABLE route_quotes ADD COLUMN IF NOT EXISTS vehicle_type text NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS shipment_orders (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),company_id uuid NOT NULL REFERENCES companies(id),
 title text NOT NULL,number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,status text NOT NULL DEFAULT 'new',
 origin text NOT NULL,destination text NOT NULL,pickup_at timestamptz,delivery_at timestamptz,
 cargo jsonb NOT NULL DEFAULT '[]',vehicle_type text NOT NULL DEFAULT '',notes text NOT NULL DEFAULT '',
 carrier_id uuid REFERENCES carriers(id),client_price numeric,client_vat text NOT NULL DEFAULT 'unknown',client_vat_rate numeric,
 carrier_price numeric,carrier_vat text NOT NULL DEFAULT 'unknown',carrier_vat_rate numeric,
 costs jsonb NOT NULL DEFAULT '[]',route jsonb NOT NULL DEFAULT '{}',loading_plan_id uuid REFERENCES loading_plans(id),
 revision integer NOT NULL DEFAULT 0,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS call_records (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),company_id uuid NOT NULL REFERENCES companies(id),
 contact_name text NOT NULL DEFAULT '',phone text NOT NULL DEFAULT '',result text NOT NULL,description text NOT NULL DEFAULT '',
 called_at timestamptz NOT NULL,next_contact_at timestamptz,order_id uuid REFERENCES shipment_orders(id),created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS company_contacts (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),company_id uuid NOT NULL REFERENCES companies(id),
 name text NOT NULL,position text NOT NULL DEFAULT '',phones text NOT NULL DEFAULT '',email text NOT NULL DEFAULT '',description text NOT NULL DEFAULT '',revision integer NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS followup_tasks (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),company_id uuid REFERENCES companies(id),order_id uuid REFERENCES shipment_orders(id),
 title text NOT NULL,description text NOT NULL DEFAULT '',due_at timestamptz NOT NULL,status text NOT NULL DEFAULT 'open',priority text NOT NULL DEFAULT 'normal',
 revision integer NOT NULL DEFAULT 0,created_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz);
CREATE TABLE IF NOT EXISTS order_quotes (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),order_id uuid NOT NULL REFERENCES shipment_orders(id),carrier_id uuid REFERENCES carriers(id),
 amount_rub numeric NOT NULL CHECK(amount_rub>=0),vehicle_type text NOT NULL,vat text NOT NULL,vat_rate numeric,
 as_of timestamptz NOT NULL,valid_until timestamptz,source text NOT NULL,source_url text NOT NULL DEFAULT '',
 description text NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS carrier_feedback (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),carrier_id uuid NOT NULL REFERENCES carriers(id),
 order_id uuid REFERENCES shipment_orders(id),rating integer NOT NULL CHECK(rating BETWEEN 1 AND 5),description text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS search_presets (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),name text NOT NULL,criteria jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS provider_cache (
 cache_key text PRIMARY KEY,payload jsonb NOT NULL,checked_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS backup_records (
 id uuid PRIMARY KEY,path text NOT NULL,status text NOT NULL DEFAULT 'created',size_bytes bigint NOT NULL DEFAULT 0,
 sha256 text NOT NULL DEFAULT '',verified_at timestamptz,error text NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS orders_owner_status ON shipment_orders(employee_id,status,updated_at DESC);
CREATE INDEX IF NOT EXISTS tasks_owner_due ON followup_tasks(employee_id,status,due_at);
CREATE INDEX IF NOT EXISTS calls_owner_company ON call_records(employee_id,company_id,called_at DESC);
CREATE INDEX IF NOT EXISTS quotes_order_date ON order_quotes(order_id,as_of DESC);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['shipment_orders','call_records','company_contacts','followup_tasks','order_quotes','carrier_feedback','search_presets'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('DROP POLICY IF EXISTS scope_read ON %I',tab);
  EXECUTE format('DROP POLICY IF EXISTS scope_write ON %I',tab);
  EXECUTE format('CREATE POLICY scope_read ON %I FOR SELECT USING (nexsearch_can_read(employee_id))',tab);
  EXECUTE format('CREATE POLICY scope_write ON %I FOR ALL USING (employee_id::text=current_setting(''nexol.employee_id'',true) OR current_setting(''nexol.is_admin'',true)=''true'') WITH CHECK(employee_id::text=current_setting(''nexol.employee_id'',true) OR current_setting(''nexol.is_admin'',true)=''true'')',tab);
 END LOOP;
END $$;
DROP POLICY IF EXISTS plans_private ON loading_plans;
DROP POLICY IF EXISTS plans_scope_read ON loading_plans;
DROP POLICY IF EXISTS plans_scope_write ON loading_plans;
CREATE POLICY plans_scope_read ON loading_plans FOR SELECT USING (nexsearch_can_read(employee_id));
CREATE POLICY plans_scope_write ON loading_plans FOR ALL USING (employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true') WITH CHECK (employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true');
CREATE OR REPLACE FUNCTION nexsearch_call_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Журнал звонков доступен только для добавления'; END $$;
DROP TRIGGER IF EXISTS calls_immutable ON call_records;
CREATE TRIGGER calls_immutable BEFORE UPDATE OR DELETE ON call_records FOR EACH ROW EXECUTE FUNCTION nexsearch_call_immutable();
INSERT INTO schema_meta(version) VALUES(14) ON CONFLICT DO NOTHING;

-- Compatibility bootstrap for the installed 1.4 updater.
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
