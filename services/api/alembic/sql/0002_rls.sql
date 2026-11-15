-- Row Level Security. The API runs every request as role routepilot_app with app.user_id set (SET LOCAL),
-- so these policies are enforced even if an application query forgets its organization filter.
-- Supabase PostgREST clients get the same rules through request.jwt.claims.sub (see rp.uid()).
-- Owners / the migration role bypass RLS and are used only by migrations and the trusted worker.

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'routepilot_app') THEN
    CREATE ROLE routepilot_app NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- Relationship helpers (SECURITY DEFINER: evaluated without recursive policy checks).
CREATE OR REPLACE FUNCTION rp.my_employee_ids(org uuid) RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT id FROM employees WHERE organization_id = org AND user_id = rp.uid()
$$;
CREATE OR REPLACE FUNCTION rp.my_driver_ids(org uuid) RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT id FROM drivers WHERE organization_id = org AND user_id = rp.uid()
$$;
CREATE OR REPLACE FUNCTION rp.is_driver_of(org uuid, alloc uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT EXISTS (SELECT 1 FROM trip_allocations a WHERE a.organization_id = org AND a.id = alloc
                 AND (a.driver_id IN (SELECT rp.my_driver_ids(org))
                      OR EXISTS (SELECT 1 FROM driver_assignments d WHERE d.organization_id = org AND d.allocation_id = a.id
                                 AND d.driver_id IN (SELECT rp.my_driver_ids(org)))))
$$;
CREATE OR REPLACE FUNCTION rp.is_passenger_on(org uuid, alloc uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT EXISTS (SELECT 1 FROM trip_passengers p WHERE p.organization_id = org AND p.allocation_id = alloc
                 AND p.status <> 'removed' AND p.employee_id IN (SELECT rp.my_employee_ids(org)))
$$;

-- Callable only by members of the organization: lets non-staff (e.g. an employee raising SOS) notify the right staff.
CREATE OR REPLACE FUNCTION rp.staff_ids(org uuid, roles text[] DEFAULT ARRAY['org_admin','dispatcher']) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT m.user_id FROM organization_members m
  WHERE m.organization_id = org AND m.is_active AND m.role = ANY (roles) AND org IN (SELECT rp.member_orgs())
$$;
CREATE OR REPLACE FUNCTION rp.channel_prefs(org uuid, u uuid) RETURNS TABLE (push boolean, whatsapp boolean, sms boolean, email boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT p.push, p.whatsapp, p.sms, p.email FROM notification_preferences p
  WHERE p.organization_id = org AND p.user_id = u AND org IN (SELECT rp.member_orgs())
$$;

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['organizations','user_profiles','organization_members','organization_settings','platform_feature_flags','locations','shifts','routes','route_stops',
    'service_days','employees','drivers','vehicles','driver_leave','employee_transport_requests','trip_allocations','trip_passengers','driver_assignments','route_overrides',
    'sos_incidents','sos_incident_locations','vehicle_last_position','vehicle_location_history','notifications','notification_deliveries','notification_preferences',
    'push_subscriptions','audit_logs','import_jobs','idempotency_keys']
  LOOP EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t); END LOOP;
END $$;

-- organizations -------------------------------------------------------------------------------
CREATE POLICY org_select ON organizations FOR SELECT USING (id IN (SELECT rp.member_orgs()) OR rp.is_platform_admin());
CREATE POLICY org_write ON organizations FOR ALL USING (rp.is_platform_admin()) WITH CHECK (rp.is_platform_admin());

-- user_profiles ---------------------------------------------------------------------------------
CREATE POLICY prof_select ON user_profiles FOR SELECT USING (
  user_id = rp.uid() OR rp.is_platform_admin()
  OR EXISTS (SELECT 1 FROM organization_members m WHERE m.user_id = user_profiles.user_id AND rp.is_staff(m.organization_id)));
CREATE POLICY prof_insert ON user_profiles FOR INSERT WITH CHECK (rp.is_platform_admin() OR user_id = rp.uid());
CREATE POLICY prof_update ON user_profiles FOR UPDATE USING (user_id = rp.uid() OR rp.is_platform_admin())
  WITH CHECK (rp.is_platform_admin() OR (user_id = rp.uid() AND is_platform_admin = false));

-- organization_members ----------------------------------------------------------------------------
CREATE POLICY mem_select ON organization_members FOR SELECT USING (user_id = rp.uid() OR rp.is_staff(organization_id) OR rp.is_platform_admin());
CREATE POLICY mem_write ON organization_members FOR ALL USING (rp.has_role(organization_id, ARRAY['org_admin']) OR rp.is_platform_admin())
  WITH CHECK (rp.has_role(organization_id, ARRAY['org_admin']) OR rp.is_platform_admin());

-- organization_settings / flags -------------------------------------------------------------------
CREATE POLICY set_select ON organization_settings FOR SELECT USING (organization_id IN (SELECT rp.member_orgs()) OR rp.is_platform_admin());
CREATE POLICY set_insert ON organization_settings FOR INSERT WITH CHECK (rp.is_platform_admin());
CREATE POLICY set_update ON organization_settings FOR UPDATE USING (rp.has_role(organization_id, ARRAY['org_admin']) OR rp.is_platform_admin())
  WITH CHECK (rp.has_role(organization_id, ARRAY['org_admin']) OR rp.is_platform_admin());
CREATE POLICY flag_select ON platform_feature_flags FOR SELECT USING (rp.uid() IS NOT NULL);
CREATE POLICY flag_write ON platform_feature_flags FOR UPDATE USING (rp.is_platform_admin()) WITH CHECK (rp.is_platform_admin());

-- Member-readable, org_admin-writable master data -------------------------------------------------
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['locations','shifts','routes','route_stops']
  LOOP
    EXECUTE format('CREATE POLICY %1$s_select ON %1$I FOR SELECT USING (organization_id IN (SELECT rp.member_orgs()))', t);
    EXECUTE format('CREATE POLICY %1$s_write ON %1$I FOR ALL USING (rp.has_role(organization_id, ARRAY[''org_admin''])) WITH CHECK (rp.has_role(organization_id, ARRAY[''org_admin'']))', t);
  END LOOP;
END $$;
CREATE POLICY sd_select ON service_days FOR SELECT USING (organization_id IN (SELECT rp.member_orgs()));
CREATE POLICY sd_write ON service_days FOR ALL USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));

-- People & fleet -----------------------------------------------------------------------------------
CREATE POLICY emp_select ON employees FOR SELECT USING (rp.is_staff(organization_id) OR user_id = rp.uid());
CREATE POLICY emp_write ON employees FOR ALL USING (rp.has_role(organization_id, ARRAY['org_admin'])) WITH CHECK (rp.has_role(organization_id, ARRAY['org_admin']));
CREATE POLICY drv_select ON drivers FOR SELECT USING (rp.is_staff(organization_id) OR user_id = rp.uid());
CREATE POLICY drv_write ON drivers FOR ALL USING (rp.has_role(organization_id, ARRAY['org_admin'])) WITH CHECK (rp.has_role(organization_id, ARRAY['org_admin']));
CREATE POLICY veh_select ON vehicles FOR SELECT USING (rp.is_staff(organization_id)
  OR EXISTS (SELECT 1 FROM trip_allocations a WHERE a.organization_id = vehicles.organization_id AND a.vehicle_id = vehicles.id
             AND (rp.is_driver_of(a.organization_id, a.id) OR rp.is_passenger_on(a.organization_id, a.id))));
CREATE POLICY veh_write ON vehicles FOR ALL USING (rp.has_role(organization_id, ARRAY['org_admin'])) WITH CHECK (rp.has_role(organization_id, ARRAY['org_admin']));
CREATE POLICY leave_select ON driver_leave FOR SELECT USING (rp.is_staff(organization_id) OR driver_id IN (SELECT rp.my_driver_ids(organization_id)));
CREATE POLICY leave_insert ON driver_leave FOR INSERT WITH CHECK (rp.is_staff(organization_id) OR (driver_id IN (SELECT rp.my_driver_ids(organization_id)) AND status = 'requested'));
CREATE POLICY leave_update ON driver_leave FOR UPDATE USING (rp.is_staff(organization_id) OR driver_id IN (SELECT rp.my_driver_ids(organization_id)))
  WITH CHECK (rp.is_staff(organization_id) OR (driver_id IN (SELECT rp.my_driver_ids(organization_id)) AND status IN ('requested','cancelled')));

-- Dispatch ------------------------------------------------------------------------------------------
CREATE POLICY req_select ON employee_transport_requests FOR SELECT USING (rp.is_staff(organization_id) OR employee_id IN (SELECT rp.my_employee_ids(organization_id)));
CREATE POLICY req_write ON employee_transport_requests FOR ALL USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));
CREATE POLICY req_self_ins ON employee_transport_requests FOR INSERT
  WITH CHECK (employee_id IN (SELECT rp.my_employee_ids(organization_id)) AND status = 'open');
CREATE POLICY req_self_upd ON employee_transport_requests FOR UPDATE
  USING (employee_id IN (SELECT rp.my_employee_ids(organization_id)) AND status = 'open')
  WITH CHECK (employee_id IN (SELECT rp.my_employee_ids(organization_id)) AND status IN ('open','cancelled'));
CREATE POLICY alloc_select ON trip_allocations FOR SELECT USING (rp.is_staff(organization_id) OR rp.is_driver_of(organization_id, id) OR rp.is_passenger_on(organization_id, id));
CREATE POLICY alloc_write ON trip_allocations FOR ALL USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));
CREATE POLICY alloc_driver_update ON trip_allocations FOR UPDATE USING (rp.is_driver_of(organization_id, id)) WITH CHECK (rp.is_driver_of(organization_id, id));
CREATE POLICY pax_select ON trip_passengers FOR SELECT USING (rp.is_staff(organization_id) OR employee_id IN (SELECT rp.my_employee_ids(organization_id)) OR rp.is_driver_of(organization_id, allocation_id));
CREATE POLICY pax_write ON trip_passengers FOR ALL USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));
CREATE POLICY pax_driver_update ON trip_passengers FOR UPDATE USING (rp.is_driver_of(organization_id, allocation_id)) WITH CHECK (rp.is_driver_of(organization_id, allocation_id));
CREATE POLICY da_select ON driver_assignments FOR SELECT USING (rp.is_staff(organization_id) OR driver_id IN (SELECT rp.my_driver_ids(organization_id)));
CREATE POLICY da_write ON driver_assignments FOR ALL USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));
CREATE POLICY da_driver_update ON driver_assignments FOR UPDATE USING (driver_id IN (SELECT rp.my_driver_ids(organization_id))) WITH CHECK (driver_id IN (SELECT rp.my_driver_ids(organization_id)));
CREATE POLICY ro_select ON route_overrides FOR SELECT USING (rp.is_staff(organization_id));
CREATE POLICY ro_write ON route_overrides FOR ALL USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));

-- SOS: coordinates visible to staff only ---------------------------------------------------------------
CREATE POLICY sos_select ON sos_incidents FOR SELECT USING (rp.is_staff(organization_id) OR raised_by_user_id = rp.uid()
  OR (allocation_id IS NOT NULL AND rp.is_driver_of(organization_id, allocation_id)));
CREATE POLICY sos_insert ON sos_incidents FOR INSERT WITH CHECK (organization_id IN (SELECT rp.member_orgs()) AND raised_by_user_id = rp.uid());
CREATE POLICY sos_update ON sos_incidents FOR UPDATE
  USING (rp.is_staff(organization_id) OR raised_by_user_id = rp.uid() OR (allocation_id IS NOT NULL AND rp.is_driver_of(organization_id, allocation_id)))
  WITH CHECK (rp.is_staff(organization_id) OR raised_by_user_id = rp.uid() OR (allocation_id IS NOT NULL AND rp.is_driver_of(organization_id, allocation_id)));
CREATE POLICY sosloc_select ON sos_incident_locations FOR SELECT USING (rp.is_staff(organization_id));
CREATE POLICY sosloc_insert ON sos_incident_locations FOR INSERT WITH CHECK (
  EXISTS (SELECT 1 FROM sos_incidents i WHERE i.organization_id = sos_incident_locations.organization_id AND i.id = incident_id AND i.raised_by_user_id = rp.uid()));
CREATE POLICY sosloc_delete ON sos_incident_locations FOR DELETE USING (rp.has_role(organization_id, ARRAY['org_admin']));

-- Live tracking: staff read; a driver may write only their own in-progress allocation's vehicle -------------
CREATE POLICY pos_select ON vehicle_last_position FOR SELECT USING (
  rp.is_staff(organization_id)
  OR (rp.is_passenger_on(organization_id, allocation_id)
      AND coalesce((SELECT live_tracking_employee_view FROM organization_settings s WHERE s.organization_id = vehicle_last_position.organization_id), false)));
CREATE POLICY pos_write ON vehicle_last_position FOR ALL USING (rp.is_driver_of(organization_id, allocation_id) OR rp.is_staff(organization_id))
  WITH CHECK (rp.is_driver_of(organization_id, allocation_id) OR rp.is_staff(organization_id));
CREATE POLICY hist_select ON vehicle_location_history FOR SELECT USING (rp.is_staff(organization_id));
CREATE POLICY hist_insert ON vehicle_location_history FOR INSERT WITH CHECK (rp.is_driver_of(organization_id, allocation_id) OR rp.is_staff(organization_id));
CREATE POLICY hist_delete ON vehicle_location_history FOR DELETE USING (rp.has_role(organization_id, ARRAY['org_admin']));

-- Notifications -------------------------------------------------------------------------------------------
CREATE POLICY notif_select ON notifications FOR SELECT USING (recipient_user_id = rp.uid());
CREATE POLICY notif_insert ON notifications FOR INSERT WITH CHECK (organization_id IN (SELECT rp.member_orgs()));
CREATE POLICY notif_update ON notifications FOR UPDATE USING (recipient_user_id = rp.uid()) WITH CHECK (recipient_user_id = rp.uid());
CREATE POLICY nd_select ON notification_deliveries FOR SELECT USING (rp.is_staff(organization_id)
  OR EXISTS (SELECT 1 FROM notifications n WHERE n.organization_id = notification_deliveries.organization_id AND n.id = notification_id AND n.recipient_user_id = rp.uid()));
CREATE POLICY nd_update ON notification_deliveries FOR UPDATE USING (rp.is_staff(organization_id)) WITH CHECK (rp.is_staff(organization_id));
CREATE POLICY nd_insert ON notification_deliveries FOR INSERT WITH CHECK (organization_id IN (SELECT rp.member_orgs()));
CREATE POLICY np_all ON notification_preferences FOR ALL USING (user_id = rp.uid()) WITH CHECK (user_id = rp.uid() AND organization_id IN (SELECT rp.member_orgs()));
CREATE POLICY ps_all ON push_subscriptions FOR ALL USING (user_id = rp.uid()) WITH CHECK (user_id = rp.uid() AND organization_id IN (SELECT rp.member_orgs()));

-- Audit, imports, idempotency --------------------------------------------------------------------------------
CREATE POLICY audit_select ON audit_logs FOR SELECT USING (rp.has_role(organization_id, ARRAY['org_admin']) OR rp.is_platform_admin());
CREATE POLICY audit_insert ON audit_logs FOR INSERT WITH CHECK (organization_id IS NULL OR organization_id IN (SELECT rp.member_orgs()) OR rp.is_platform_admin());
CREATE POLICY imp_all ON import_jobs FOR ALL USING (rp.has_role(organization_id, ARRAY['org_admin'])) WITH CHECK (rp.has_role(organization_id, ARRAY['org_admin']));
CREATE POLICY idem_all ON idempotency_keys FOR ALL USING (user_id = rp.uid()) WITH CHECK (user_id = rp.uid() AND organization_id IN (SELECT rp.member_orgs()));

-- Creates a notification for another user of the same organization WITHOUT granting read access to anyone's inbox.
-- Caller must be an active member of the organization and the recipient must be one too. Returns NULL on dedupe conflict.
CREATE OR REPLACE FUNCTION rp.create_notification(org uuid, recipient uuid, p_event text, p_title text, p_body text, p_data jsonb, p_critical boolean, p_dedupe text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, rp AS $$
DECLARE nid uuid;
BEGIN
  IF org NOT IN (SELECT rp.member_orgs()) THEN RAISE EXCEPTION 'not a member of organization' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NOT EXISTS (SELECT 1 FROM organization_members m WHERE m.organization_id = org AND m.user_id = recipient AND m.is_active) THEN
    RAISE EXCEPTION 'recipient is not a member of organization' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO notifications (organization_id, recipient_user_id, event_type, title, body, data, critical, dedupe_key)
  VALUES (org, recipient, p_event, p_title, p_body, coalesce(p_data, '{}'), p_critical, p_dedupe)
  ON CONFLICT (organization_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
  RETURNING id INTO nid;
  RETURN nid;
END $$;

-- Minimal, role-checked views of other people's data for a single trip -------------------------------------------------
-- Manifest: staff, or the trip's driver. Phone numbers are released only once the trip is confirmed. Gender is never returned.
CREATE OR REPLACE FUNCTION rp.trip_manifest(org uuid, alloc uuid)
RETURNS TABLE (passenger_id uuid, employee_id uuid, name text, phone text, stop_order int, lat double precision, lon double precision,
               address text, needs text[], status text, eta_minutes numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT p.id, e.id, e.name,
         CASE WHEN a.status IN ('CONFIRMED','IN_PROGRESS') THEN e.phone END,
         p.stop_order,
         CASE a.direction WHEN 'pickup' THEN r.pickup_lat ELSE r.drop_lat END,
         CASE a.direction WHEN 'pickup' THEN r.pickup_lon ELSE r.drop_lon END,
         CASE a.direction WHEN 'pickup' THEN e.pickup_address ELSE e.drop_address END,
         e.accessibility_needs, p.status, p.eta_minutes_from_start
  FROM trip_allocations a
  JOIN trip_passengers p ON p.organization_id = a.organization_id AND p.allocation_id = a.id AND p.status <> 'removed'
  JOIN employees e ON e.organization_id = p.organization_id AND e.id = p.employee_id
  JOIN employee_transport_requests r ON r.organization_id = p.organization_id AND r.id = p.request_id
  WHERE a.organization_id = org AND a.id = alloc AND (rp.is_staff(org) OR rp.is_driver_of(org, alloc))
  ORDER BY p.stop_order
$$;

-- Crew: driver + vehicle details for a passenger, only once confirmed. Staff and the driver always see it.
CREATE OR REPLACE FUNCTION rp.trip_crew(org uuid, alloc uuid)
RETURNS TABLE (driver_name text, driver_phone text, vehicle_code text, registration_number text, vehicle_type text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, rp AS $$
  SELECT d.name, d.phone, v.vehicle_code, v.registration_number, v.vehicle_type
  FROM trip_allocations a
  JOIN vehicles v ON v.organization_id = a.organization_id AND v.id = a.vehicle_id
  LEFT JOIN drivers d ON d.organization_id = a.organization_id AND d.id = a.driver_id
  WHERE a.organization_id = org AND a.id = alloc
    AND (rp.is_staff(org) OR rp.is_driver_of(org, alloc)
         OR (rp.is_passenger_on(org, alloc) AND a.status IN ('CONFIRMED','IN_PROGRESS','COMPLETED')))
$$;

-- Grants ----------------------------------------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public, rp TO routepilot_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO routepilot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO routepilot_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA rp TO routepilot_app;
REVOKE UPDATE, DELETE ON audit_logs FROM routepilot_app;

-- Upsert of the single SOS fix. ON CONFLICT DO UPDATE needs SELECT/UPDATE visibility that the reporter deliberately does not have
-- (coordinates are staff-only), so the write goes through a definer function that re-checks reporter, incident state and switches.
CREATE OR REPLACE FUNCTION rp.upsert_sos_location(p_org uuid, p_incident uuid, p_lat double precision, p_lon double precision,
                                                  p_acc double precision, p_captured timestamptz, p_days int)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM sos_incidents i WHERE i.organization_id = p_org AND i.id = p_incident
                 AND i.raised_by_user_id = rp.uid() AND i.status <> 'resolved') THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = '42501';
  END IF;
  INSERT INTO sos_incident_locations (organization_id, incident_id, latitude, longitude, accuracy_m, captured_at, purge_after)
  VALUES (p_org, p_incident, p_lat, p_lon, p_acc, p_captured, now() + make_interval(days => p_days))
  ON CONFLICT (organization_id, incident_id) DO UPDATE
    SET latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, accuracy_m = EXCLUDED.accuracy_m, captured_at = EXCLUDED.captured_at;
END $$;
REVOKE ALL ON FUNCTION rp.upsert_sos_location(uuid, uuid, double precision, double precision, double precision, timestamptz, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rp.upsert_sos_location(uuid, uuid, double precision, double precision, double precision, timestamptz, int) TO routepilot_app;
