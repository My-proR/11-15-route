-- Driver trip features: (1) driver self-service availability, (2) per-passenger boarding code the driver must enter to mark boarded (OFF by default, organization setting).
-- The code is never readable by the driver role: verification runs in a SECURITY DEFINER function that counts failed attempts and locks out brute force. The API must COMMIT after a failed attempt so the counter persists (it returns the error instead of raising).

ALTER TABLE organization_settings ADD COLUMN boarding_code_required boolean NOT NULL DEFAULT false;
ALTER TABLE drivers ADD COLUMN availability_updated_at timestamptz;

-- Drivers may not UPDATE their own row (admin-only policy), so the on/off duty switch goes through a definer function that touches only is_available.
CREATE OR REPLACE FUNCTION rp.set_my_availability(p_org uuid, p_available boolean) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp, pg_temp AS $$
BEGIN
  UPDATE drivers SET is_available = p_available, availability_updated_at = now(), updated_at = now()
   WHERE organization_id = p_org AND user_id = rp.uid() AND is_active;
  IF NOT FOUND THEN RAISE EXCEPTION 'not permitted' USING ERRCODE = '42501'; END IF;
  RETURN p_available;
END $$;
REVOKE ALL ON FUNCTION rp.set_my_availability(uuid, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rp.set_my_availability(uuid, boolean) TO routepilot_app;

CREATE TABLE trip_boarding_codes (
  organization_id uuid NOT NULL,
  passenger_id uuid NOT NULL,
  allocation_id uuid NOT NULL,
  code text NOT NULL CHECK (code ~ '^[0-9]{4}$'),
  failed_attempts int NOT NULL DEFAULT 0,
  locked_until timestamptz,
  verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, passenger_id),
  FOREIGN KEY (organization_id, passenger_id) REFERENCES trip_passengers (organization_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_boarding_codes_alloc ON trip_boarding_codes (organization_id, allocation_id);

ALTER TABLE trip_boarding_codes ENABLE ROW LEVEL SECURITY;
-- Boarding code: visible to the passenger it belongs to and to staff; never to drivers. Writes only via the definer functions below.
CREATE POLICY tbc_select ON trip_boarding_codes FOR SELECT USING (
  rp.is_staff(organization_id)
  OR EXISTS (SELECT 1 FROM trip_passengers p WHERE p.organization_id = trip_boarding_codes.organization_id AND p.id = trip_boarding_codes.passenger_id
             AND p.employee_id IN (SELECT rp.my_employee_ids(trip_boarding_codes.organization_id))));
GRANT SELECT, INSERT, UPDATE, DELETE ON trip_boarding_codes TO routepilot_app;
REVOKE INSERT, UPDATE, DELETE ON trip_boarding_codes FROM routepilot_app;

-- Creates missing 4-digit codes for the live passengers of an allocation (CSPRNG-backed via gen_random_uuid). Idempotent.
CREATE OR REPLACE FUNCTION rp.ensure_boarding_codes(p_org uuid, p_alloc uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp, pg_temp AS $$
BEGIN
  IF NOT (rp.is_staff(p_org) OR rp.is_driver_of(p_org, p_alloc) OR rp.is_passenger_on(p_org, p_alloc)) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = '42501';
  END IF;
  INSERT INTO trip_boarding_codes (organization_id, passenger_id, allocation_id, code)
  SELECT p.organization_id, p.id, p.allocation_id,
         lpad((('x' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 8))::bit(32)::bigint % 10000)::text, 4, '0')
  FROM trip_passengers p JOIN trip_allocations a ON a.organization_id = p.organization_id AND a.id = p.allocation_id
  WHERE p.organization_id = p_org AND p.allocation_id = p_alloc AND p.status <> 'removed' AND a.status IN ('CONFIRMED', 'IN_PROGRESS')
  ON CONFLICT (organization_id, passenger_id) DO NOTHING;
END $$;

-- Returns: ok | not_driver | not_found | not_in_progress | already_boarded | locked | invalid. On ok the passenger becomes 'boarded'.
CREATE OR REPLACE FUNCTION rp.verify_boarding_code(p_org uuid, p_alloc uuid, p_passenger uuid, p_code text, p_max_attempts int DEFAULT 5, p_lock_minutes int DEFAULT 10)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp, pg_temp AS $$
DECLARE c trip_boarding_codes%ROWTYPE; ps text; als text;
BEGIN
  IF NOT rp.is_driver_of(p_org, p_alloc) THEN RETURN 'not_driver'; END IF;
  SELECT p.status, a.status INTO ps, als FROM trip_passengers p JOIN trip_allocations a ON a.organization_id = p.organization_id AND a.id = p.allocation_id
   WHERE p.organization_id = p_org AND p.id = p_passenger AND p.allocation_id = p_alloc AND p.status <> 'removed';
  IF NOT FOUND THEN RETURN 'not_found'; END IF;
  IF als <> 'IN_PROGRESS' THEN RETURN 'not_in_progress'; END IF;
  IF ps = 'boarded' THEN RETURN 'already_boarded'; END IF;
  PERFORM rp.ensure_boarding_codes(p_org, p_alloc);
  SELECT * INTO c FROM trip_boarding_codes WHERE organization_id = p_org AND passenger_id = p_passenger FOR UPDATE;
  IF NOT FOUND THEN RETURN 'not_found'; END IF;
  IF c.locked_until IS NOT NULL AND c.locked_until > now() THEN RETURN 'locked'; END IF;
  IF c.code = p_code THEN
    UPDATE trip_boarding_codes SET verified_at = now(), failed_attempts = 0, locked_until = NULL WHERE organization_id = p_org AND passenger_id = p_passenger;
    UPDATE trip_passengers SET status = 'boarded' WHERE organization_id = p_org AND id = p_passenger;
    RETURN 'ok';
  END IF;
  IF c.failed_attempts + 1 >= p_max_attempts THEN
    UPDATE trip_boarding_codes SET failed_attempts = 0, locked_until = now() + make_interval(mins => p_lock_minutes) WHERE organization_id = p_org AND passenger_id = p_passenger;
  ELSE
    UPDATE trip_boarding_codes SET failed_attempts = failed_attempts + 1 WHERE organization_id = p_org AND passenger_id = p_passenger;
  END IF;
  RETURN 'invalid';
END $$;

REVOKE ALL ON FUNCTION rp.ensure_boarding_codes(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION rp.verify_boarding_code(uuid, uuid, uuid, text, int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rp.ensure_boarding_codes(uuid, uuid) TO routepilot_app;
GRANT EXECUTE ON FUNCTION rp.verify_boarding_code(uuid, uuid, uuid, text, int, int) TO routepilot_app;

-- Passenger self-boarding after scanning the driver's (short-lived, signed) trip QR. The API verifies the QR; this function re-checks that the caller is a
-- live passenger of an IN_PROGRESS trip and flips only their own row. Returns: ok | already_boarded | not_passenger | not_in_progress
CREATE OR REPLACE FUNCTION rp.board_self(p_org uuid, p_alloc uuid) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp, pg_temp AS $$
DECLARE pid uuid; ps text; als text;
BEGIN
  SELECT a.status INTO als FROM trip_allocations a WHERE a.organization_id = p_org AND a.id = p_alloc;
  IF NOT FOUND THEN RETURN 'not_passenger'; END IF;
  SELECT p.id, p.status INTO pid, ps FROM trip_passengers p
   WHERE p.organization_id = p_org AND p.allocation_id = p_alloc AND p.status <> 'removed' AND p.employee_id IN (SELECT rp.my_employee_ids(p_org)) LIMIT 1;
  IF pid IS NULL THEN RETURN 'not_passenger'; END IF;
  IF als <> 'IN_PROGRESS' THEN RETURN 'not_in_progress'; END IF;
  IF ps = 'boarded' THEN RETURN 'already_boarded'; END IF;
  UPDATE trip_passengers SET status = 'boarded' WHERE organization_id = p_org AND id = pid AND status = 'assigned';
  UPDATE trip_boarding_codes SET verified_at = now() WHERE organization_id = p_org AND passenger_id = pid;
  RETURN 'ok';
END $$;
REVOKE ALL ON FUNCTION rp.board_self(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rp.board_self(uuid, uuid) TO routepilot_app;
