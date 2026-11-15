import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useAuth } from "./auth";

export interface Me { user_id: string; email: string; full_name: string; language?: "en" | "ta"; organization_suspended?: boolean; role: "org_admin" | "dispatcher" | "driver" | "employee" | null; active_organization_id: string | null; organizations: { organization_id: string; name: string; role: string }[] }
export interface FeatureState { effective: boolean; blocked_by: string | null }
export const useMe = () => { const t = useAuth((s) => s.token); const o = useAuth((s) => s.orgId); return useQuery({ queryKey: ["me", t, o], queryFn: () => api<Me>("/me"), enabled: Boolean(t), retry: false }); };
export const useFeatures = () => { const { data } = useMe(); return useQuery({ queryKey: ["features", data?.active_organization_id], queryFn: () => api<{ sos_gps: FeatureState; live_tracking: FeatureState }>("/features"), enabled: Boolean(data?.active_organization_id), staleTime: 30_000 }); };
