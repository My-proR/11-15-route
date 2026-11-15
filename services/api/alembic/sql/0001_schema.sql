-- RoutePilot core schema. Applied by Alembic revision 0001 (and exported for Supabase by scripts/export_supabase_sql.sh).
-- Conventions: uuid keys, organization_id on every tenant table, composite (organization_id, id) FKs so a child
-- row can never reference a parent in another organization, text+CHECK instead of enums for easy migration.

CREATE SCHEMA IF NOT EXISTS rp;

CREATE OR REPLACE FUNCTION rp.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$;

-- ---------------------------------------------------------------------------------------------
-- Platform / identity
-- ---------------------------------------------------------------------------------------------
CREATE TABLE organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(name) BETWEEN 2 AND 160),
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  timezone text NOT NULL DEFAULT 'Asia/Kolkata',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_profiles (
  user_id uuid PRIMARY KEY,                       -- equals auth.users.id (Supabase) / JWT "sub"
  email text NOT NULL,
  full_name text NOT NULL DEFAULT '',
  phone text,
  is_platform_admin boolean NOT NULL DEFAULT false,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_user_profiles_email ON user_profiles (lower(email));

CREATE TABLE organization_members (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('org_admin','dispatcher','driver','employee')),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, user_id)
);
CREATE INDEX ix_members_user ON organization_members (user_id) WHERE is_active;

CREATE TABLE organization_settings (
  organization_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  -- SOS GPS (effective = GPS_SOS_FEATURE_ENABLED env AND platform flag 'sos_gps' AND this column)
  sos_gps_enabled boolean NOT NULL DEFAULT false,
  sos_retention_days int NOT NULL DEFAULT 90 CHECK (sos_retention_days BETWEEN 1 AND 3650),
  -- Live vehicle tracking (effective = LIVE_TRACKING_FEATURE_ENABLED env AND platform flag 'live_tracking' AND this column)
  live_tracking_enabled boolean NOT NULL DEFAULT false,
  live_tracking_interval_seconds int NOT NULL DEFAULT 30 CHECK (live_tracking_interval_seconds BETWEEN 10 AND 300),
  live_tracking_history_enabled boolean NOT NULL DEFAULT false,
  live_tracking_history_retention_days int NOT NULL DEFAULT 7 CHECK (live_tracking_history_retention_days BETWEEN 1 AND 365),
  live_tracking_employee_view boolean NOT NULL DEFAULT false,
  -- Dispatch policy
  max_detour_ratio numeric(4,2) NOT NULL DEFAULT 1.50 CHECK (max_detour_ratio BETWEEN 1.0 AND 5.0),
  max_trip_minutes int NOT NULL DEFAULT 90 CHECK (max_trip_minutes BETWEEN 10 AND 480),
  require_dispatcher_confirmation boolean NOT NULL DEFAULT true,
  driver_acceptance_timeout_minutes int NOT NULL DEFAULT 30 CHECK (driver_acceptance_timeout_minutes BETWEEN 1 AND 1440),
  last_drop_policy_enabled boolean NOT NULL DEFAULT false,
  local_trip_target_per_day int NOT NULL DEFAULT 2 CHECK (local_trip_target_per_day BETWEEN 0 AND 10),
  local_trip_max_km numeric(6,2) NOT NULL DEFAULT 10 CHECK (local_trip_max_km > 0),
  local_trips_excluded_from_km_fairness boolean NOT NULL DEFAULT false,
  fairness_weights jsonb NOT NULL DEFAULT '{"planned_km":1.0,"trips":0.5,"duty_minutes":0.3,"deadhead_km":0.4}',
  fairness_window_days int NOT NULL DEFAULT 7 CHECK (fairness_window_days BETWEEN 1 AND 90),
  notification_channels jsonb NOT NULL DEFAULT '{"push":true,"whatsapp":false,"sms":false,"email":false}',
  updated_by uuid REFERENCES user_profiles(user_id),
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform_feature_flags (
  key text PRIMARY KEY CHECK (key IN ('sos_gps','live_tracking','whatsapp','sms')),
  enabled boolean NOT NULL DEFAULT false,
  description text NOT NULL DEFAULT '',
  updated_by uuid REFERENCES user_profiles(user_id),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO platform_feature_flags(key, enabled, description) VALUES
  ('sos_gps', false, 'Allow SOS to capture GPS coordinates (also needs GPS_SOS_FEATURE_ENABLED env and organization setting)'),
  ('live_tracking', false, 'Allow live vehicle tracking during in-progress trips (also needs LIVE_TRACKING_FEATURE_ENABLED env and organization setting)'),
  ('whatsapp', false, 'Allow WhatsApp notifications when provider credentials exist'),
  ('sms', false, 'Allow SMS notifications when provider credentials exist');

-- ---------------------------------------------------------------------------------------------
-- Access helper functions (SECURITY DEFINER so policies never recurse). Identity comes from either
-- the API (SET LOCAL app.user_id) or Supabase PostgREST (request.jwt.claims.sub).
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rp.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT coalesce(
    nullif(current_setting('app.user_id', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )::uuid
$$;

CREATE OR REPLACE FUNCTION rp.is_platform_admin() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT coalesce((SELECT is_platform_admin AND is_active FROM user_profiles WHERE user_id = rp.uid()), false)
$$;

CREATE OR REPLACE FUNCTION rp.member_orgs() RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT m.organization_id FROM organization_members m
  JOIN organizations o ON o.id = m.organization_id AND o.status = 'active'
  WHERE m.user_id = rp.uid() AND m.is_active
$$;

CREATE OR REPLACE FUNCTION rp.has_role(org uuid, roles text[]) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT EXISTS (
    SELECT 1 FROM organization_members m
    JOIN organizations o ON o.id = m.organization_id AND o.status = 'active'
    WHERE m.organization_id = org AND m.user_id = rp.uid() AND m.is_active AND m.role = ANY (roles))
$$;

CREATE OR REPLACE FUNCTION rp.is_staff(org uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT rp.has_role(org, ARRAY['org_admin','dispatcher'])
$$;

-- ---------------------------------------------------------------------------------------------
-- Master data
-- ---------------------------------------------------------------------------------------------
CREATE TABLE locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('branch','office','pickup_point','drop_point','depot')),
  name text NOT NULL,
  address text NOT NULL DEFAULT '',
  latitude double precision CHECK (latitude BETWEEN -90 AND 90),
  longitude double precision CHECK (longitude BETWEEN -180 AND 180),
  geocode_source text CHECK (geocode_source IN ('user','geocoder','trusted_import')),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE TABLE shifts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  start_time time NOT NULL,
  end_time time NOT NULL,
  pickup_window_minutes int NOT NULL DEFAULT 30 CHECK (pickup_window_minutes BETWEEN 5 AND 240),
  arrival_buffer_minutes int NOT NULL DEFAULT 15 CHECK (arrival_buffer_minutes BETWEEN 0 AND 120),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, name)
);

CREATE TABLE routes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  code text NOT NULL,
  name text NOT NULL,
  branch_id uuid,
  shift_id uuid,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, code),
  FOREIGN KEY (organization_id, branch_id) REFERENCES locations (organization_id, id),
  FOREIGN KEY (organization_id, shift_id) REFERENCES shifts (organization_id, id)
);

CREATE TABLE route_stops (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  route_id uuid NOT NULL,
  location_id uuid NOT NULL,
  sequence int NOT NULL CHECK (sequence >= 0),
  UNIQUE (organization_id, route_id, sequence),
  FOREIGN KEY (organization_id, route_id) REFERENCES routes (organization_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organization_id, location_id) REFERENCES locations (organization_id, id)
);

CREATE TABLE service_days (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  service_date date NOT NULL,
  is_service_day boolean NOT NULL DEFAULT true,
  note text NOT NULL DEFAULT '',
  UNIQUE (organization_id, service_date)
);

CREATE TABLE employees (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id uuid REFERENCES user_profiles(user_id),
  employee_code text NOT NULL,
  branch_id uuid,
  name text NOT NULL,
  work_email text NOT NULL,
  phone text,
  department text,
  designation text,
  gender text CHECK (gender IN ('male','female','other')),
  shift_id uuid,
  pickup_address text NOT NULL DEFAULT '',
  pickup_lat double precision CHECK (pickup_lat BETWEEN -90 AND 90),
  pickup_lon double precision CHECK (pickup_lon BETWEEN -180 AND 180),
  drop_address text NOT NULL DEFAULT '',
  drop_lat double precision CHECK (drop_lat BETWEEN -90 AND 90),
  drop_lon double precision CHECK (drop_lon BETWEEN -180 AND 180),
  preferred_pickup_time time,
  accessibility_needs text[] NOT NULL DEFAULT '{}',
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, employee_code),
  FOREIGN KEY (organization_id, branch_id) REFERENCES locations (organization_id, id),
  FOREIGN KEY (organization_id, shift_id) REFERENCES shifts (organization_id, id),
  CHECK ((pickup_lat IS NULL) = (pickup_lon IS NULL)),
  CHECK ((drop_lat IS NULL) = (drop_lon IS NULL))
);
CREATE UNIQUE INDEX ux_employees_email ON employees (organization_id, lower(work_email));
CREATE INDEX ix_employees_user ON employees (user_id);
CREATE INDEX ix_employees_shift ON employees (organization_id, shift_id) WHERE is_active;

CREATE TABLE drivers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id uuid REFERENCES user_profiles(user_id),
  driver_code text NOT NULL,
  name text NOT NULL,
  phone text,
  license_number text NOT NULL,
  license_valid_until date NOT NULL,
  is_available boolean NOT NULL DEFAULT true,
  eligible_shift_ids uuid[] NOT NULL DEFAULT '{}',
  qualifications text[] NOT NULL DEFAULT '{}',
  local_trip_eligible boolean NOT NULL DEFAULT false,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, driver_code)
);
CREATE INDEX ix_drivers_user ON drivers (user_id);

CREATE TABLE vehicles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  vehicle_code text NOT NULL,
  registration_number text NOT NULL,
  vehicle_type text NOT NULL DEFAULT 'sedan',
  capacity int NOT NULL CHECK (capacity BETWEEN 1 AND 60),     -- passenger seats, excluding driver
  accessibility_features text[] NOT NULL DEFAULT '{}',
  availability text NOT NULL DEFAULT 'available' CHECK (availability IN ('available','unavailable','maintenance')),
  insurance_valid_until date,
  fitness_valid_until date,
  default_route_id uuid,                                        -- recurring route; never altered by a one-day override
  default_driver_id uuid,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, vehicle_code),
  UNIQUE (organization_id, registration_number),
  FOREIGN KEY (organization_id, default_route_id) REFERENCES routes (organization_id, id),
  FOREIGN KEY (organization_id, default_driver_id) REFERENCES drivers (organization_id, id)
);

CREATE TABLE driver_leave (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  driver_id uuid NOT NULL,
  starts_on date NOT NULL,
  ends_on date NOT NULL,
  reason text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','approved','rejected','cancelled')),
  decided_by uuid REFERENCES user_profiles(user_id),
  decided_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_on >= starts_on),
  FOREIGN KEY (organization_id, driver_id) REFERENCES drivers (organization_id, id),
  UNIQUE (organization_id, id)
);
CREATE INDEX ix_leave_driver_dates ON driver_leave (organization_id, driver_id, starts_on, ends_on);

-- ---------------------------------------------------------------------------------------------
-- Dispatch
-- ---------------------------------------------------------------------------------------------
CREATE TABLE employee_transport_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  employee_id uuid NOT NULL,
  service_date date NOT NULL,
  shift_id uuid NOT NULL,
  direction text NOT NULL CHECK (direction IN ('pickup','drop')),   -- pickup = home->office, drop = office->home
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','allocated','cancelled')),
  pickup_lat double precision, pickup_lon double precision,
  drop_lat double precision, drop_lon double precision,
  requested_time time,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, employee_id, service_date, shift_id, direction),
  FOREIGN KEY (organization_id, employee_id) REFERENCES employees (organization_id, id),
  FOREIGN KEY (organization_id, shift_id) REFERENCES shifts (organization_id, id)
);
CREATE INDEX ix_requests_day ON employee_transport_requests (organization_id, service_date, shift_id, status);

CREATE TABLE trip_allocations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  service_date date NOT NULL,
  shift_id uuid NOT NULL,
  direction text NOT NULL CHECK (direction IN ('pickup','drop')),
  vehicle_id uuid NOT NULL,
  driver_id uuid,
  route_id uuid,
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN
    ('DRAFT','PROPOSED','PENDING_DRIVER_ACCEPTANCE','DRIVER_ACCEPTED','CONFIRMED','IN_PROGRESS','COMPLETED','CANCELLED','REQUIRES_REASSIGNMENT')),
  capacity_snapshot int NOT NULL CHECK (capacity_snapshot > 0),
  planned_distance_km numeric(8,2),
  planned_duration_minutes numeric(7,1),
  estimate_status text NOT NULL DEFAULT 'unavailable' CHECK (estimate_status IN ('final','provisional','unavailable')),
  stop_sequence jsonb NOT NULL DEFAULT '[]',
  explanation jsonb NOT NULL DEFAULT '{}',
  is_local_trip boolean NOT NULL DEFAULT false,
  version int NOT NULL DEFAULT 1,
  cancelled_reason text,
  started_at timestamptz,
  completed_at timestamptz,
  created_by uuid REFERENCES user_profiles(user_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  FOREIGN KEY (organization_id, shift_id) REFERENCES shifts (organization_id, id),
  FOREIGN KEY (organization_id, vehicle_id) REFERENCES vehicles (organization_id, id),
  FOREIGN KEY (organization_id, driver_id) REFERENCES drivers (organization_id, id),
  FOREIGN KEY (organization_id, route_id) REFERENCES routes (organization_id, id)
);
-- A vehicle runs at most one live allocation per date/shift/direction.
CREATE UNIQUE INDEX ux_alloc_vehicle_slot ON trip_allocations (organization_id, service_date, shift_id, direction, vehicle_id)
  WHERE status <> 'CANCELLED';
CREATE INDEX ix_alloc_day ON trip_allocations (organization_id, service_date, status);
CREATE INDEX ix_alloc_driver ON trip_allocations (organization_id, driver_id, service_date);

CREATE TABLE trip_passengers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  allocation_id uuid NOT NULL,
  request_id uuid NOT NULL,
  employee_id uuid NOT NULL,
  stop_order int NOT NULL CHECK (stop_order >= 0),
  eta_minutes_from_start numeric(7,1),
  status text NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned','boarded','dropped','no_show','removed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  FOREIGN KEY (organization_id, allocation_id) REFERENCES trip_allocations (organization_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organization_id, request_id) REFERENCES employee_transport_requests (organization_id, id),
  FOREIGN KEY (organization_id, employee_id) REFERENCES employees (organization_id, id)
);
-- An employee request can be on only one live allocation.
CREATE UNIQUE INDEX ux_passenger_request ON trip_passengers (organization_id, request_id) WHERE status <> 'removed';
CREATE INDEX ix_passengers_alloc ON trip_passengers (organization_id, allocation_id);
CREATE INDEX ix_passengers_employee ON trip_passengers (organization_id, employee_id);

-- Capacity guard: serializes on the allocation row so concurrent inserts can never exceed capacity.
CREATE OR REPLACE FUNCTION rp.enforce_passenger_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cap int; cnt int;
BEGIN
  IF NEW.status = 'removed' THEN RETURN NEW; END IF;
  SELECT capacity_snapshot INTO cap FROM trip_allocations
    WHERE organization_id = NEW.organization_id AND id = NEW.allocation_id FOR UPDATE;
  SELECT count(*) INTO cnt FROM trip_passengers
    WHERE organization_id = NEW.organization_id AND allocation_id = NEW.allocation_id
      AND status <> 'removed' AND id IS DISTINCT FROM NEW.id;
  IF cnt + 1 > cap THEN
    RAISE EXCEPTION 'vehicle capacity exceeded (% of % seats)', cnt + 1, cap USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_passenger_capacity BEFORE INSERT OR UPDATE OF status, allocation_id ON trip_passengers
  FOR EACH ROW EXECUTE FUNCTION rp.enforce_passenger_capacity();

-- Vehicles cannot be allocated while unavailable / in maintenance / inactive.
CREATE OR REPLACE FUNCTION rp.enforce_vehicle_usable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v vehicles%ROWTYPE;
BEGIN
  IF NEW.status = 'CANCELLED' THEN RETURN NEW; END IF;
  IF TG_OP = 'UPDATE' AND NEW.vehicle_id = OLD.vehicle_id AND NEW.status IN ('IN_PROGRESS','COMPLETED') THEN RETURN NEW; END IF;
  SELECT * INTO v FROM vehicles WHERE organization_id = NEW.organization_id AND id = NEW.vehicle_id;
  IF NOT v.is_active OR v.availability <> 'available' THEN
    RAISE EXCEPTION 'vehicle % is not available for allocation (%)', v.vehicle_code, v.availability USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.capacity_snapshot > v.capacity THEN
    RAISE EXCEPTION 'capacity snapshot exceeds vehicle capacity' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_alloc_vehicle BEFORE INSERT OR UPDATE OF vehicle_id, status ON trip_allocations
  FOR EACH ROW EXECUTE FUNCTION rp.enforce_vehicle_usable();
CREATE TRIGGER trg_alloc_touch BEFORE UPDATE ON trip_allocations FOR EACH ROW EXECUTE FUNCTION rp.touch_updated_at();

CREATE TABLE driver_assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  allocation_id uuid NOT NULL,
  driver_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','ACCEPTED','DECLINED','EXPIRED','CANCELLED')),
  requested_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  responded_at timestamptz,
  response_reason text,
  replacement_for_leave_id uuid,
  fairness_explanation jsonb NOT NULL DEFAULT '{}',
  UNIQUE (organization_id, id),
  FOREIGN KEY (organization_id, allocation_id) REFERENCES trip_allocations (organization_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organization_id, driver_id) REFERENCES drivers (organization_id, id),
  FOREIGN KEY (organization_id, replacement_for_leave_id) REFERENCES driver_leave (organization_id, id)
);
CREATE UNIQUE INDEX ux_assignment_open ON driver_assignments (organization_id, allocation_id) WHERE status IN ('PENDING','ACCEPTED');
CREATE INDEX ix_assignment_driver ON driver_assignments (organization_id, driver_id, status);

-- One-day vehicle reroutes; the vehicle's recurring default_route_id is never modified.
CREATE TABLE route_overrides (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  vehicle_id uuid NOT NULL,
  service_date date NOT NULL,
  from_route_id uuid,
  to_route_id uuid NOT NULL,
  reason text NOT NULL CHECK (length(reason) >= 3),
  actor_user_id uuid NOT NULL REFERENCES user_profiles(user_id),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, vehicle_id) REFERENCES vehicles (organization_id, id),
  FOREIGN KEY (organization_id, from_route_id) REFERENCES routes (organization_id, id),
  FOREIGN KEY (organization_id, to_route_id) REFERENCES routes (organization_id, id)
);
CREATE UNIQUE INDEX ux_override_vehicle_day ON route_overrides (organization_id, vehicle_id, service_date) WHERE active;

-- ---------------------------------------------------------------------------------------------
-- SOS
-- ---------------------------------------------------------------------------------------------
CREATE TABLE sos_incidents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  raised_by_user_id uuid NOT NULL REFERENCES user_profiles(user_id),
  raised_by_role text NOT NULL CHECK (raised_by_role IN ('employee','driver')),
  allocation_id uuid,
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved')),
  location_status text NOT NULL DEFAULT 'not_requested' CHECK (location_status IN ('obtained','unavailable','denied','disabled','not_requested')),
  message text NOT NULL DEFAULT '' CHECK (length(message) <= 500),
  acknowledged_by uuid REFERENCES user_profiles(user_id),
  acknowledged_at timestamptz,
  resolved_by uuid REFERENCES user_profiles(user_id),
  resolved_at timestamptz,
  resolution_note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, raised_by_user_id, idempotency_key),
  FOREIGN KEY (organization_id, allocation_id) REFERENCES trip_allocations (organization_id, id)
);
CREATE INDEX ix_sos_status ON sos_incidents (organization_id, status, created_at DESC);

CREATE TABLE sos_incident_locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  incident_id uuid NOT NULL,
  latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
  longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
  accuracy_m double precision CHECK (accuracy_m >= 0),
  captured_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  purge_after timestamptz NOT NULL,
  UNIQUE (organization_id, incident_id),
  FOREIGN KEY (organization_id, incident_id) REFERENCES sos_incidents (organization_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_sos_loc_purge ON sos_incident_locations (purge_after);

-- Defence in depth: coordinates can only be inserted while the platform flag AND the organization setting are on.
-- (The deployment env flag is enforced by the API before it ever reaches this insert.)
CREATE OR REPLACE FUNCTION rp.enforce_sos_gps_enabled() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp AS $$
BEGIN
  IF NOT coalesce((SELECT enabled FROM platform_feature_flags WHERE key = 'sos_gps'), false)
     OR NOT coalesce((SELECT sos_gps_enabled FROM organization_settings WHERE organization_id = NEW.organization_id), false) THEN
    RAISE EXCEPTION 'SOS GPS is disabled; coordinates cannot be stored' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_sos_loc_flag BEFORE INSERT ON sos_incident_locations FOR EACH ROW EXECUTE FUNCTION rp.enforce_sos_gps_enabled();

-- ---------------------------------------------------------------------------------------------
-- Live vehicle tracking (default OFF). One row per vehicle (latest position); optional history only when enabled.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE vehicle_last_position (
  organization_id uuid NOT NULL,
  vehicle_id uuid NOT NULL,
  allocation_id uuid NOT NULL,
  latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
  longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
  accuracy_m double precision CHECK (accuracy_m >= 0),
  speed_kmh double precision CHECK (speed_kmh BETWEEN 0 AND 300),
  heading double precision CHECK (heading BETWEEN 0 AND 360),
  captured_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, vehicle_id),
  FOREIGN KEY (organization_id, vehicle_id) REFERENCES vehicles (organization_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organization_id, allocation_id) REFERENCES trip_allocations (organization_id, id) ON DELETE CASCADE
);

CREATE TABLE vehicle_location_history (
  id bigserial PRIMARY KEY,
  organization_id uuid NOT NULL,
  vehicle_id uuid NOT NULL,
  allocation_id uuid NOT NULL,
  latitude double precision NOT NULL,
  longitude double precision NOT NULL,
  accuracy_m double precision,
  captured_at timestamptz NOT NULL,
  purge_after timestamptz NOT NULL,
  FOREIGN KEY (organization_id, vehicle_id) REFERENCES vehicles (organization_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_veh_hist ON vehicle_location_history (organization_id, vehicle_id, captured_at DESC);
CREATE INDEX ix_veh_hist_purge ON vehicle_location_history (purge_after);

CREATE OR REPLACE FUNCTION rp.enforce_live_tracking_enabled() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp AS $$
BEGIN
  IF NOT coalesce((SELECT enabled FROM platform_feature_flags WHERE key = 'live_tracking'), false)
     OR NOT coalesce((SELECT live_tracking_enabled FROM organization_settings WHERE organization_id = NEW.organization_id), false) THEN
    RAISE EXCEPTION 'live tracking is disabled; positions cannot be stored' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_last_pos_flag BEFORE INSERT OR UPDATE ON vehicle_last_position FOR EACH ROW EXECUTE FUNCTION rp.enforce_live_tracking_enabled();
CREATE OR REPLACE FUNCTION rp.enforce_live_history_enabled() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp AS $$
BEGIN
  IF NOT coalesce((SELECT enabled FROM platform_feature_flags WHERE key = 'live_tracking'), false)
     OR NOT coalesce((SELECT live_tracking_enabled AND live_tracking_history_enabled FROM organization_settings WHERE organization_id = NEW.organization_id), false) THEN
    RAISE EXCEPTION 'live tracking history is disabled' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_hist_flag BEFORE INSERT ON vehicle_location_history FOR EACH ROW EXECUTE FUNCTION rp.enforce_live_history_enabled();

-- ---------------------------------------------------------------------------------------------
-- Notifications
-- ---------------------------------------------------------------------------------------------
CREATE TABLE notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  recipient_user_id uuid NOT NULL REFERENCES user_profiles(user_id),
  event_type text NOT NULL,
  title text NOT NULL,
  body text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}',
  critical boolean NOT NULL DEFAULT false,
  dedupe_key text,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, id)
);
CREATE UNIQUE INDEX ux_notif_dedupe ON notifications (organization_id, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX ix_notif_recipient ON notifications (recipient_user_id, created_at DESC);

CREATE TABLE notification_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  notification_id uuid NOT NULL,
  channel text NOT NULL CHECK (channel IN ('in_app','push_web','push_expo','whatsapp','sms','email')),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','delivered','failed','skipped')),
  provider_message_id text,
  attempts int NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error text,
  sent_at timestamptz,
  delivered_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (notification_id, channel),
  FOREIGN KEY (organization_id, notification_id) REFERENCES notifications (organization_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_deliv_due ON notification_deliveries (next_attempt_at) WHERE status = 'pending';

CREATE TABLE notification_preferences (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  push boolean NOT NULL DEFAULT true,
  whatsapp boolean NOT NULL DEFAULT false,
  sms boolean NOT NULL DEFAULT false,
  email boolean NOT NULL DEFAULT false,
  PRIMARY KEY (organization_id, user_id)
);

CREATE TABLE push_subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('web','expo')),
  token text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, token)
);

-- ---------------------------------------------------------------------------------------------
-- Audit, imports, idempotency
-- ---------------------------------------------------------------------------------------------
CREATE TABLE audit_logs (
  id bigserial PRIMARY KEY,
  organization_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  actor_user_id uuid,
  action text NOT NULL,
  entity_type text NOT NULL,
  entity_id text,
  details jsonb NOT NULL DEFAULT '{}',
  ip inet,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_org ON audit_logs (organization_id, created_at DESC);
CREATE INDEX ix_audit_action ON audit_logs (organization_id, action, created_at DESC);

CREATE OR REPLACE FUNCTION rp.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_logs is append-only' USING ERRCODE = 'insufficient_privilege'; END $$;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION rp.audit_immutable();

CREATE TABLE import_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind text NOT NULL DEFAULT 'employees' CHECK (kind IN ('employees')),
  filename text NOT NULL,
  content_hash text NOT NULL,
  status text NOT NULL DEFAULT 'previewed' CHECK (status IN ('previewed','committed','failed')),
  mapping jsonb NOT NULL DEFAULT '{}',
  parsed_rows jsonb NOT NULL DEFAULT '[]',
  total_rows int NOT NULL DEFAULT 0,
  valid_rows int NOT NULL DEFAULT 0,
  error_rows int NOT NULL DEFAULT 0,
  duplicate_rows int NOT NULL DEFAULT 0,
  errors jsonb NOT NULL DEFAULT '[]',
  created_by uuid REFERENCES user_profiles(user_id),
  committed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_keys (
  organization_id uuid NOT NULL,
  user_id uuid NOT NULL,
  scope text NOT NULL,
  key text NOT NULL,
  request_hash text NOT NULL,
  response jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_id, scope, key)
);

-- updated_at triggers
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['organizations','user_profiles','organization_settings','locations','shifts','routes','employees','drivers','vehicles','platform_feature_flags']
  LOOP EXECUTE format('CREATE TRIGGER trg_%s_touch BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION rp.touch_updated_at()', t, t); END LOOP;
END $$;
