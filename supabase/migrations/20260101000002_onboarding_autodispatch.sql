-- Easy onboarding (invite links), language preference, and auto-dispatch at a cutoff time.

-- ---- language ------------------------------------------------------------------------------------------------
ALTER TABLE user_profiles ADD COLUMN language text NOT NULL DEFAULT 'en' CHECK (language IN ('en', 'ta'));

CREATE OR REPLACE FUNCTION rp.set_my_language(p_lang text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp, pg_temp AS $$
BEGIN
  IF p_lang NOT IN ('en', 'ta') THEN RAISE EXCEPTION 'unsupported language' USING ERRCODE = '22023'; END IF;
  UPDATE user_profiles SET language = p_lang, updated_at = now() WHERE user_id = rp.uid();
END $$;
REVOKE ALL ON FUNCTION rp.set_my_language(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rp.set_my_language(text) TO routepilot_app;

-- ---- invite links ----------------------------------------------------------------------------------------------
-- The raw token is shown once to the admin (to paste into WhatsApp); only its SHA-256 hash is stored.
CREATE TABLE org_invites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('dispatcher', 'driver', 'employee')),
  driver_id uuid,
  employee_id uuid,
  token_hash text NOT NULL UNIQUE,
  email text,                                   -- optional: when set, only this Google/email account can accept
  created_by uuid NOT NULL REFERENCES user_profiles(user_id),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  used_by uuid REFERENCES user_profiles(user_id),
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, driver_id) REFERENCES drivers (organization_id, id) ON DELETE CASCADE,
  FOREIGN KEY (organization_id, employee_id) REFERENCES employees (organization_id, id) ON DELETE CASCADE,
  CHECK ((role = 'driver') = (driver_id IS NOT NULL)),
  CHECK ((role = 'employee') = (employee_id IS NOT NULL))
);
CREATE INDEX ix_invites_org ON org_invites (organization_id, created_at DESC);
ALTER TABLE org_invites ENABLE ROW LEVEL SECURITY;
CREATE POLICY inv_staff ON org_invites FOR ALL USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));
GRANT SELECT, INSERT, UPDATE ON org_invites TO routepilot_app;

-- ---- auto-dispatch -----------------------------------------------------------------------------------------------
ALTER TABLE organization_settings
  ADD COLUMN auto_dispatch_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN auto_dispatch_cutoff time NOT NULL DEFAULT '18:00',
  ADD COLUMN auto_dispatch_days_ahead int NOT NULL DEFAULT 1 CHECK (auto_dispatch_days_ahead BETWEEN 1 AND 3),
  ADD COLUMN auto_dispatch_send_to_drivers boolean NOT NULL DEFAULT true;

CREATE TABLE auto_dispatch_runs (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  service_date date NOT NULL,
  ran_at timestamptz NOT NULL DEFAULT now(),
  summary jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (organization_id, service_date)
);
ALTER TABLE auto_dispatch_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY adr_select ON auto_dispatch_runs FOR SELECT USING (rp.is_staff(organization_id));
GRANT SELECT ON auto_dispatch_runs TO routepilot_app;
