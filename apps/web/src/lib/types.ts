export type Role = "org_admin" | "dispatcher" | "driver" | "employee";
export interface OrgRef { organization_id: string; name: string; slug: string; role: Role }
export interface Me {
  user_id: string; email: string; full_name: string; language?: "en" | "ta"; is_platform_admin: boolean;
  active_organization_id: string | null; role: Role | null; organization_suspended?: boolean; organizations: OrgRef[];
}
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface FeatureState { deployment_enabled: boolean; platform_enabled: boolean; organization_enabled: boolean; effective: boolean; blocked_by: "deployment" | "platform" | "organization" | null }
export interface Features { sos_gps: FeatureState; live_tracking: FeatureState }
export type AllocationStatus = "DRAFT" | "PROPOSED" | "PENDING_DRIVER_ACCEPTANCE" | "DRIVER_ACCEPTED" | "CONFIRMED" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED" | "REQUIRES_REASSIGNMENT";
export interface Allocation {
  id: string; service_date: string; direction: "pickup" | "drop"; status: AllocationStatus; version: number;
  vehicle_id: string; vehicle_code?: string; driver_id: string | null; driver_name?: string | null; shift_id: string;
  planned_distance_km: number | null; planned_duration_minutes: number | null; estimate_status: string; is_local_trip: boolean;
  passenger_count?: number; capacity_snapshot?: number;
}
export interface ApiErrorBody { error: { code: string; message: string; details?: unknown } }
