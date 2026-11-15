-- Platform billing: per cab-day usage charged to the organisation (default Rs 50 + GST), monthly invoices, overdue suspension.
ALTER TABLE organizations ADD COLUMN suspended_reason text CHECK (suspended_reason IN ('billing', 'manual'));
UPDATE organizations SET suspended_reason = 'manual' WHERE status = 'suspended';

CREATE TABLE organization_billing (
  organization_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  billing_enabled boolean NOT NULL DEFAULT false,
  rate_paise int NOT NULL DEFAULT 5000 CHECK (rate_paise BETWEEN 0 AND 10000000),
  gst_percent numeric(4,1) NOT NULL DEFAULT 18 CHECK (gst_percent BETWEEN 0 AND 28),
  free_until date,                                     -- cab-days on or before this date are free (pilot period)
  due_days int NOT NULL DEFAULT 7 CHECK (due_days BETWEEN 0 AND 60),
  grace_days int NOT NULL DEFAULT 7 CHECK (grace_days BETWEEN 0 AND 60),   -- extra days after the due date before access is paused
  billing_email text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE invoice_number_seq;
CREATE TABLE invoices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  invoice_number text NOT NULL UNIQUE,
  period_start date NOT NULL,
  period_end date NOT NULL,
  cab_days int NOT NULL CHECK (cab_days >= 0),
  billable_cab_days int NOT NULL CHECK (billable_cab_days >= 0),
  rate_paise int NOT NULL,
  subtotal_paise bigint NOT NULL,
  gst_percent numeric(4,1) NOT NULL,
  gst_paise bigint NOT NULL,
  total_paise bigint NOT NULL,
  status text NOT NULL DEFAULT 'issued' CHECK (status IN ('issued', 'paid', 'void')),
  issued_at timestamptz NOT NULL DEFAULT now(),
  due_date date NOT NULL,
  paid_at timestamptz,
  payment_ref text,
  detail jsonb NOT NULL DEFAULT '[]',                  -- per-day cab counts, so the invoice can be checked line by line
  UNIQUE (organization_id, period_start)
);
CREATE INDEX ix_invoices_status ON invoices (status, due_date);

CREATE OR REPLACE FUNCTION rp.is_org_admin(org uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT EXISTS (SELECT 1 FROM organization_members m WHERE m.organization_id = org AND m.user_id = rp.uid() AND m.role = 'org_admin' AND m.is_active)
$$;
REVOKE ALL ON FUNCTION rp.is_org_admin(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rp.is_org_admin(uuid) TO routepilot_app;

ALTER TABLE organization_billing ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
CREATE POLICY ob_select ON organization_billing FOR SELECT USING (rp.is_platform_admin() OR rp.is_org_admin(organization_id));
CREATE POLICY ob_write ON organization_billing FOR ALL USING (rp.is_platform_admin()) WITH CHECK (rp.is_platform_admin());
CREATE POLICY inv_select ON invoices FOR SELECT USING (rp.is_platform_admin() OR rp.is_org_admin(organization_id));
CREATE POLICY inv_write ON invoices FOR ALL USING (rp.is_platform_admin()) WITH CHECK (rp.is_platform_admin());
GRANT SELECT, INSERT, UPDATE ON organization_billing, invoices TO routepilot_app;
GRANT USAGE ON SEQUENCE invoice_number_seq TO routepilot_app;

-- A paused (suspended) organisation is invisible through RLS (rp.member_orgs() only returns active ones). People still need to
-- learn that they belong to it, and org admins need to reach Billing to resolve it, so this definer function lists the caller's OWN
-- memberships including paused organisations. It returns nothing about anyone else.
CREATE OR REPLACE FUNCTION rp.my_orgs() RETURNS TABLE (organization_id uuid, name text, slug text, role text, suspended boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT m.organization_id, o.name, o.slug, m.role, (o.status <> 'active')
  FROM organization_members m JOIN organizations o ON o.id = m.organization_id
  WHERE m.user_id = rp.uid() AND m.is_active
$$;
REVOKE ALL ON FUNCTION rp.my_orgs() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rp.my_orgs() TO routepilot_app;
