import { Link } from "react-router-dom";
import { Upload } from "lucide-react";
import { Badge, Button } from "../components/ui";
import { ResourcePage, type FieldDef } from "../components/ResourcePage";
import { useMe } from "../lib/hooks";
import { fmtDate } from "../lib/format";

const writable = (role?: string | null) => role === "org_admin" || role === "dispatcher";

export function Employees() {
  const role = useMe().data?.role;
  const fields: FieldDef[] = [
    { name: "employee_code", label: "Employee code", type: "text", required: true },
    { name: "name", label: "Full name", type: "text", required: true },
    { name: "work_email", label: "Work email", type: "email", required: true },
    { name: "phone", label: "Phone", type: "tel", hint: "+91 98765 43210" },
    { name: "department", label: "Department", type: "text" },
    { name: "designation", label: "Designation", type: "text" },
    { name: "gender", label: "Gender", type: "select", options: [{ value: "female", label: "Female" }, { value: "male", label: "Male" }, { value: "other", label: "Other" }], hint: "Used only for policy rules the organization configures." },
    { name: "branch_id", label: "Office / branch", type: "select", lookup: "branches" },
    { name: "shift_id", label: "Shift", type: "select", lookup: "shifts" },
    { name: "preferred_pickup_time", label: "Preferred pickup time", type: "time" },
    { name: "pickup_address", label: "Pickup address", type: "text", wide: true },
    { name: "pickup_lat", label: "Pickup latitude", type: "number", min: -90, max: 90, pair: "pickup_lon" },
    { name: "pickup_lon", label: "Pickup longitude", type: "number", min: -180, max: 180 },
    { name: "drop_address", label: "Drop address", type: "text", wide: true },
    { name: "drop_lat", label: "Drop latitude", type: "number", min: -90, max: 90, pair: "drop_lon" },
    { name: "drop_lon", label: "Drop longitude", type: "number", min: -180, max: 180 },
    { name: "accessibility_needs", label: "Accessibility needs", type: "tags", hint: "Comma separated, e.g. wheelchair", wide: true },
    { name: "is_active", label: "Active", type: "checkbox" },
  ];
  return (
    <ResourcePage title="Employees" subtitle="People who use company transport." endpoint="/employees" singular="employee" fields={fields} canWrite={writable(role)}
      extraActions={role === "org_admin" ? <Link to="/employees/import"><Button variant="secondary"><Upload className="size-4" />Import CSV / Excel</Button></Link> : undefined}
      columns={[{ key: "employee_code", header: "Code" }, { key: "name", header: "Name" }, { key: "work_email", header: "Email" }, { key: "department", header: "Department" },
        { key: "pickup_address", header: "Pickup", render: (r) => String(r.pickup_address || "—") }]} />
  );
}

export function Drivers() {
  const role = useMe().data?.role;
  const fields: FieldDef[] = [
    { name: "driver_code", label: "Driver code", type: "text", required: true },
    { name: "name", label: "Full name", type: "text", required: true },
    { name: "phone", label: "Phone", type: "tel" },
    { name: "license_number", label: "Licence number", type: "text", required: true },
    { name: "license_valid_until", label: "Licence valid until", type: "date", required: true },
    { name: "eligible_shift_ids", label: "Eligible shifts", type: "select", lookup: "shifts", multi: true, wide: true, hint: "Leave empty to make the driver ineligible for planning." },
    { name: "qualifications", label: "Qualifications", type: "tags", hint: "Comma separated" },
    { name: "local_trip_eligible", label: "Eligible for short local trips", type: "checkbox" },
    { name: "is_available", label: "Available for duty", type: "checkbox" },
    { name: "is_active", label: "Active", type: "checkbox" },
  ];
  return (
    <ResourcePage title="Drivers" subtitle="Licence validity and shift eligibility are enforced when assigning." endpoint="/drivers" singular="driver" fields={fields} canWrite={writable(role)}
      columns={[{ key: "driver_code", header: "Code" }, { key: "name", header: "Name" }, { key: "phone", header: "Phone" },
        { key: "license_valid_until", header: "Licence valid until", render: (r) => { const d = String(r.license_valid_until); const expired = new Date(d) < new Date(); return <span className={expired ? "text-bad" : undefined}>{fmtDate(d)}{expired && " (expired)"}</span>; } },
        { key: "is_available", header: "Duty", render: (r) => <Badge tone={r.is_available ? "ok" : "warn"}>{r.is_available ? "available" : "off duty"}</Badge> }]} />
  );
}

export function Vehicles() {
  const role = useMe().data?.role;
  const fields: FieldDef[] = [
    { name: "vehicle_code", label: "Vehicle code", type: "text", required: true },
    { name: "registration_number", label: "Registration number", type: "text", required: true },
    { name: "vehicle_type", label: "Type", type: "text" },
    { name: "capacity", label: "Passenger seats (excluding driver)", type: "number", required: true, min: 1, max: 60, step: 1 },
    { name: "availability", label: "Availability", type: "select", required: true, options: [{ value: "available", label: "Available" }, { value: "unavailable", label: "Unavailable" }, { value: "maintenance", label: "Under maintenance" }] },
    { name: "insurance_valid_until", label: "Insurance valid until", type: "date" },
    { name: "fitness_valid_until", label: "Fitness valid until", type: "date" },
    { name: "default_driver_id", label: "Default driver", type: "select", lookup: "drivers" },
    { name: "default_route_id", label: "Default route", type: "select", lookup: "routes" },
    { name: "accessibility_features", label: "Accessibility features", type: "tags", hint: "Comma separated, e.g. wheelchair" },
    { name: "is_active", label: "Active", type: "checkbox" },
  ];
  return (
    <ResourcePage title="Vehicles" subtitle="Capacity counts passenger seats only; the driver is not a passenger." endpoint="/vehicles" singular="vehicle" fields={fields} canWrite={writable(role)}
      columns={[{ key: "vehicle_code", header: "Code" }, { key: "registration_number", header: "Registration" }, { key: "capacity", header: "Seats" },
        { key: "availability", header: "Availability", render: (r) => <Badge tone={r.availability === "available" ? "ok" : "bad"}>{String(r.availability)}</Badge> },
        { key: "insurance_valid_until", header: "Insurance", render: (r) => fmtDate(r.insurance_valid_until as string | null) }, { key: "fitness_valid_until", header: "Fitness", render: (r) => fmtDate(r.fitness_valid_until as string | null) }]} />
  );
}

export function Locations() {
  const role = useMe().data?.role;
  const fields: FieldDef[] = [
    { name: "kind", label: "Kind", type: "select", required: true, options: ["branch", "office", "pickup_point", "drop_point", "depot"].map((k) => ({ value: k, label: k.replace("_", " ") })) },
    { name: "name", label: "Name", type: "text", required: true },
    { name: "address", label: "Address", type: "text", wide: true },
    { name: "latitude", label: "Latitude", type: "number", min: -90, max: 90, pair: "longitude" },
    { name: "longitude", label: "Longitude", type: "number", min: -180, max: 180 },
    { name: "geocode_source", label: "Coordinate source", type: "select", options: [{ value: "user", label: "Entered / confirmed by a person" }, { value: "geocoder", label: "Geocoder" }, { value: "trusted_import", label: "Trusted import" }], hint: "Routing needs confirmed coordinates." },
    { name: "is_active", label: "Active", type: "checkbox" },
  ];
  return <ResourcePage title="Locations" subtitle="Offices, branches and pickup/drop points." endpoint="/locations" singular="location" fields={fields} canWrite={writable(role)}
    columns={[{ key: "name", header: "Name" }, { key: "kind", header: "Kind" }, { key: "address", header: "Address" }, { key: "coords", header: "Coordinates", render: (r) => (r.latitude != null ? `${Number(r.latitude).toFixed(5)}, ${Number(r.longitude).toFixed(5)}` : <Badge tone="warn">missing</Badge>) }]} />;
}

export function Shifts() {
  const role = useMe().data?.role;
  const fields: FieldDef[] = [
    { name: "name", label: "Name", type: "text", required: true },
    { name: "start_time", label: "Start time", type: "time", required: true },
    { name: "end_time", label: "End time", type: "time", required: true },
    { name: "pickup_window_minutes", label: "Pickup window (minutes)", type: "number", min: 5, max: 240, step: 1 },
    { name: "arrival_buffer_minutes", label: "Arrival buffer (minutes)", type: "number", min: 0, max: 120, step: 1 },
    { name: "is_active", label: "Active", type: "checkbox" },
  ];
  return <ResourcePage title="Shifts" endpoint="/shifts" singular="shift" fields={fields} canWrite={writable(role)}
    columns={[{ key: "name", header: "Name" }, { key: "start_time", header: "Start", render: (r) => String(r.start_time).slice(0, 5) }, { key: "end_time", header: "End", render: (r) => String(r.end_time).slice(0, 5) },
      { key: "pickup_window_minutes", header: "Pickup window (min)" }]} />;
}
