CREATE TABLE IF NOT EXISTS employees (
 id uuid PRIMARY KEY, login text NOT NULL UNIQUE, first_name text NOT NULL, last_name text NOT NULL,
 password_hash text NOT NULL, is_admin boolean NOT NULL DEFAULT false, active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS sessions (
 token_hash text PRIMARY KEY, employee_id uuid NOT NULL REFERENCES employees(id), device_id text NOT NULL,
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS devices (
 id text PRIMARY KEY, employee_id uuid REFERENCES employees(id), computer_name text NOT NULL DEFAULT '',
 ip text NOT NULL DEFAULT '', last_seen timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS companies (
 id uuid PRIMARY KEY, name text NOT NULL, inn text, payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS companies_inn ON companies(inn) WHERE inn IS NOT NULL;
CREATE TABLE IF NOT EXISTS company_identities (identity text PRIMARY KEY,company_id uuid NOT NULL REFERENCES companies(id));
ALTER TABLE companies ADD COLUMN IF NOT EXISTS state text NOT NULL DEFAULT 'ready';
CREATE TABLE IF NOT EXISTS company_signatures (signature text NOT NULL,company_id uuid NOT NULL REFERENCES companies(id),PRIMARY KEY(signature,company_id));
CREATE TABLE IF NOT EXISTS employee_leads (
 company_id uuid PRIMARY KEY REFERENCES companies(id),employee_id uuid NOT NULL REFERENCES employees(id),
 status text NOT NULL DEFAULT 'new' CHECK(status IN ('new','processing','refused','taken')),
 called boolean NOT NULL DEFAULT false,call_note text NOT NULL DEFAULT '',called_at timestamptz,
 revision integer NOT NULL DEFAULT 0,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(employee_id,company_id));
CREATE INDEX IF NOT EXISTS leads_owner ON employee_leads(employee_id,status);
CREATE TABLE IF NOT EXISTS employee_clients (
 company_id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),stage text NOT NULL DEFAULT '',
 active boolean NOT NULL DEFAULT true,revision integer NOT NULL DEFAULT 0,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(employee_id,company_id) REFERENCES employee_leads(employee_id,company_id));
CREATE INDEX IF NOT EXISTS clients_owner_stage ON employee_clients(employee_id,active,stage);
CREATE TABLE IF NOT EXISTS carriers (
 id uuid PRIMARY KEY,name text NOT NULL,contacts text NOT NULL DEFAULT '',routes text NOT NULL DEFAULT '',description text NOT NULL DEFAULT '',
 created_by uuid NOT NULL REFERENCES employees(id),updated_by uuid NOT NULL REFERENCES employees(id),revision integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS loading_plans (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),name text NOT NULL,project jsonb NOT NULL,
 revision integer NOT NULL DEFAULT 0,updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS jobs (
 id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),target integer,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','partial','error','stopped')),
 added integer NOT NULL DEFAULT 0,duplicates integer NOT NULL DEFAULT 0,message text NOT NULL DEFAULT '',
 stop_requested boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_employee ON jobs(employee_id) WHERE status IN ('queued','running');
ALTER TABLE employee_leads ADD COLUMN IF NOT EXISTS job_id uuid REFERENCES jobs(id);
CREATE TABLE IF NOT EXISTS scraper_candidates (
 url text PRIMARY KEY,source text NOT NULL,payload jsonb,state text NOT NULL DEFAULT 'pending',attempts integer NOT NULL DEFAULT 0,last_attempt uuid,error text);
CREATE INDEX IF NOT EXISTS candidate_queue ON scraper_candidates(source,state,attempts);
CREATE TABLE IF NOT EXISTS scraper_pages (url text PRIMARY KEY,source text NOT NULL,last_scan timestamptz);
CREATE TABLE IF NOT EXISTS audit_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,employee_id uuid REFERENCES employees(id),kind text NOT NULL,
 entity_id text NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE employee_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE employee_leads FORCE ROW LEVEL SECURITY;
ALTER TABLE employee_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE employee_clients FORCE ROW LEVEL SECURITY;
ALTER TABLE loading_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE loading_plans FORCE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS leads_private ON employee_leads;
CREATE POLICY leads_private ON employee_leads USING
 (employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true')
 WITH CHECK(employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true');
DROP POLICY IF EXISTS clients_private ON employee_clients;
CREATE POLICY clients_private ON employee_clients USING
 (employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true')
 WITH CHECK(employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true');
DROP POLICY IF EXISTS plans_private ON loading_plans;
CREATE POLICY plans_private ON loading_plans USING
 (employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true')
 WITH CHECK(employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true');
DROP POLICY IF EXISTS jobs_private ON jobs;
CREATE POLICY jobs_private ON jobs USING
 (employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true')
 WITH CHECK(employee_id::text=current_setting('nexol.employee_id',true) OR current_setting('nexol.is_admin',true)='true');

-- NexSearch 1.3: positions, criteria, finances, personal routes and append-only history.
CREATE TABLE IF NOT EXISTS schema_meta(version integer PRIMARY KEY,installed_at timestamptz DEFAULT now());
ALTER TABLE employees ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'logist' CHECK(role IN ('logist','manager','director'));
ALTER TABLE employees ADD COLUMN IF NOT EXISTS requested_role text NOT NULL DEFAULT 'logist' CHECK(requested_role IN ('logist','manager','director'));
ALTER TABLE employees ADD COLUMN IF NOT EXISTS department text NOT NULL DEFAULT 'main';
UPDATE employees SET role='director',requested_role='director' WHERE is_admin AND role<>'director';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS active_verified boolean NOT NULL DEFAULT false;
ALTER TABLE employee_clients ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT '';
ALTER TABLE employee_clients ADD COLUMN IF NOT EXISTS destination text NOT NULL DEFAULT '';
ALTER TABLE employee_clients ADD COLUMN IF NOT EXISTS cargo text NOT NULL DEFAULT '';
ALTER TABLE employee_clients ADD COLUMN IF NOT EXISTS weight_kg numeric;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS criteria jsonb NOT NULL DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS rejected integer NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS rejection_reasons jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS search_preferences(employee_id uuid PRIMARY KEY REFERENCES employees(id),criteria jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS company_checks(inn text PRIMARY KEY,payload jsonb NOT NULL,checked_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_stages(employee_id uuid REFERENCES employees(id),name text NOT NULL,position integer NOT NULL DEFAULT 0,PRIMARY KEY(employee_id,name));
CREATE TABLE IF NOT EXISTS route_quotes(id uuid PRIMARY KEY,employee_id uuid NOT NULL REFERENCES employees(id),origin text NOT NULL,destination text NOT NULL,
 vehicle text NOT NULL DEFAULT '',source text NOT NULL,amount_rub numeric NOT NULL CHECK(amount_rub>=0),vat text NOT NULL DEFAULT '',note text NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS host_settings(name text PRIMARY KEY,value jsonb NOT NULL,updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS details jsonb NOT NULL DEFAULT '{}';
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS ip text NOT NULL DEFAULT '';
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS previous_hash text NOT NULL DEFAULT '';
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS event_hash text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS audit_actor_time ON audit_events(employee_id,created_at DESC);
CREATE INDEX IF NOT EXISTS quotes_owner_route ON route_quotes(employee_id,origin,destination,created_at DESC);
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE OR REPLACE FUNCTION nexsearch_audit_seal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(73142009);
 NEW.id:=nextval(pg_get_serial_sequence('audit_events','id'));
 SELECT event_hash INTO NEW.previous_hash FROM audit_events ORDER BY id DESC LIMIT 1;
 NEW.previous_hash:=COALESCE(NEW.previous_hash,'');
 NEW.event_hash:=encode(digest(NEW.previous_hash||NEW.id::text||COALESCE(NEW.employee_id::text,'')||NEW.kind||NEW.entity_id||NEW.details::text||NEW.created_at::text,'sha256'),'hex');
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS audit_seal ON audit_events;
CREATE TRIGGER audit_seal BEFORE INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION nexsearch_audit_seal();
CREATE OR REPLACE FUNCTION nexsearch_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'История действий доступна только для добавления'; END $$;
DROP TRIGGER IF EXISTS audit_immutable ON audit_events;
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION nexsearch_audit_immutable();
CREATE OR REPLACE FUNCTION nexsearch_can_read(owner uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT owner::text=current_setting('nexol.employee_id',true)
 OR current_setting('nexol.is_admin',true)='true'
 OR current_setting('nexol.role',true)='director'
 OR (current_setting('nexol.role',true)='manager' AND EXISTS(SELECT 1 FROM employees e WHERE e.id=owner AND e.role='logist' AND e.department=current_setting('nexol.department',true)))
$$;
DROP POLICY IF EXISTS leads_private ON employee_leads;
DROP POLICY IF EXISTS clients_private ON employee_clients;
DROP POLICY IF EXISTS plans_private ON loading_plans;
DROP POLICY IF EXISTS jobs_private ON jobs;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['employee_leads','employee_clients','loading_plans','jobs','search_preferences','workspace_stages','route_quotes'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('DROP POLICY IF EXISTS scope_read ON %I',tab);
  EXECUTE format('DROP POLICY IF EXISTS scope_write ON %I',tab);
  EXECUTE format('CREATE POLICY scope_read ON %I FOR SELECT USING (nexsearch_can_read(employee_id))',tab);
  EXECUTE format('CREATE POLICY scope_write ON %I FOR ALL USING (employee_id::text=current_setting(''nexol.employee_id'',true) OR current_setting(''nexol.is_admin'',true)=''true'') WITH CHECK (employee_id::text=current_setting(''nexol.employee_id'',true) OR current_setting(''nexol.is_admin'',true)=''true'')',tab);
 END LOOP;
END $$;
INSERT INTO schema_meta(version) VALUES(13) ON CONFLICT DO NOTHING;
