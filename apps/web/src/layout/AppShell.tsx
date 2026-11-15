import { useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Send, Bell, Building2, CalendarOff, ClipboardList, FileBarChart, Flag, HeartPulse, LayoutDashboard, LogOut, MapPin, Menu, Navigation, Route as RouteIcon, ScrollText, Settings, Siren, Receipt, Wallet, Truck, UserCog, Users, UsersRound, Clock3, TriangleAlert, Car, X, type LucideIcon } from "lucide-react";
import { useMe, useFeatures } from "../lib/hooks";
import { useSession } from "../lib/session";
import { Select, Spinner, ErrorBox, cx } from "../components/ui";
import { useQueryClient } from "@tanstack/react-query";
import type { Role } from "../lib/types";
import { useLang, useT } from "../lib/i18n";
import LanguageSwitch from "../components/LanguageSwitch";
import { ApiError } from "../lib/api";

interface Item { to: string; label: string; icon: LucideIcon; roles?: Role[]; platform?: boolean; needsTracking?: boolean }
const staff: Role[] = ["org_admin", "dispatcher"];
export const NAV: { title: string; items: Item[] }[] = [
  { title: "Operations", items: [
    { to: "/", label: "Overview", icon: LayoutDashboard, roles: staff },
    { to: "/allocations", label: "Daily allocations", icon: ClipboardList, roles: staff },
    { to: "/exceptions", label: "Exceptions", icon: TriangleAlert, roles: staff },
    { to: "/vehicle-board", label: "Vehicle board", icon: Car, roles: staff },
    { to: "/fleet", label: "Routes & fleet", icon: RouteIcon, roles: staff },
    { to: "/tracking", label: "Live tracking", icon: Navigation, roles: staff, needsTracking: true },
    { to: "/sos", label: "SOS incidents", icon: Siren, roles: staff },
    { to: "/leave", label: "Driver leave", icon: CalendarOff, roles: staff },
  ] },
  { title: "Master data", items: [
    { to: "/employees", label: "Employees", icon: UsersRound, roles: staff },
    { to: "/drivers", label: "Drivers", icon: UserCog, roles: staff },
    { to: "/vehicles", label: "Vehicles", icon: Truck, roles: staff },
    { to: "/locations", label: "Locations", icon: MapPin, roles: staff },
    { to: "/shifts", label: "Shifts", icon: Clock3, roles: staff },
    { to: "/routes", label: "Routes", icon: RouteIcon, roles: staff },
  ] },
  { title: "Insights", items: [
    { to: "/reports", label: "Reports", icon: FileBarChart, roles: staff },
    { to: "/notifications", label: "Notifications", icon: Bell, roles: [...staff, "driver", "employee"] },
    { to: "/audit", label: "Audit logs", icon: ScrollText, roles: ["org_admin"] },
  ] },
  { title: "Organization", items: [
    { to: "/members", label: "Members", icon: Users, roles: ["org_admin"] },
    { to: "/invites", label: "Invite links", icon: Send, roles: staff },
    { to: "/billing", label: "Billing", icon: Receipt, roles: ["org_admin"] },
    { to: "/settings", label: "Settings & privacy", icon: Settings, roles: ["org_admin"] },
  ] },
  { title: "My work", items: [
    { to: "/me", label: "My transport", icon: Car, roles: ["employee", "driver"] },
    { to: "/me/sos", label: "Emergency SOS", icon: Siren, roles: ["employee", "driver"] },
  ] },
  { title: "Platform", items: [
    { to: "/platform/organizations", label: "Organizations", icon: Building2, platform: true },
    { to: "/platform/billing", label: "Billing & invoices", icon: Wallet, platform: true },
    { to: "/platform/flags", label: "Feature flags", icon: Flag, platform: true },
    { to: "/platform/health", label: "System health", icon: HeartPulse, platform: true },
  ] },
];

export default function AppShell() {
  const me = useMe();
  const features = useFeatures();
  const signOut = useSession((s) => s.signOut);
  const setOrg = useSession((s) => s.setActiveOrg);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const t = useT();
  const adopt = useLang((s) => s.adopt);
  useEffect(() => { adopt(me.data?.language); }, [me.data?.language, adopt]);

  if (me.isPending) return <Spinner label="Loading your workspace" />;
  if (me.isError) {
    const unprovisioned = me.error instanceof ApiError && me.error.status === 403;
    return <div className="mx-auto max-w-md space-y-4 p-6">{unprovisioned ? <div className="rounded-xl border border-line bg-surface p-4 text-sm">{t("notSetUp")}</div> : <ErrorBox error={me.error} retry={() => void me.refetch()} />}<div className="flex items-center gap-3"><LanguageSwitch /><button className="text-sm underline" onClick={() => void signOut()}>{t("signOut")}</button></div></div>;
  }
  const u = me.data;
  const role = u.role;
  const paused = Boolean(u.organization_suspended) && !u.is_platform_admin;
  if (paused && role !== "org_admin") {
    return <div className="mx-auto max-w-md space-y-4 p-6"><div className="rounded-xl border border-line bg-surface p-4 text-sm">{t("paused")}</div><div className="flex items-center gap-3"><LanguageSwitch /><button className="text-sm underline" onClick={() => void signOut()}>{t("signOut")}</button></div></div>;
  }
  if (paused && !loc.pathname.startsWith("/billing")) return <Navigate to="/billing" replace />;
  const visible = NAV.map((g) => ({ ...g, items: g.items.filter((i) => (i.platform ? u.is_platform_admin : role && (!i.roles || i.roles.includes(role)) && (!paused || i.to === "/billing")) && (!i.needsTracking || features.data?.live_tracking.effective)) })).filter((g) => g.items.length);

  const nav = (
    <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
      {visible.map((g) => (
        <div key={g.title}>
          <p className="px-2 pb-1 text-xs font-semibold uppercase tracking-wide text-muted">{g.title}</p>
          {g.items.map((i) => (
            <NavLink key={i.to} to={i.to} end={i.to === "/" || i.to === "/me"} onClick={() => setOpen(false)}
              className={({ isActive }) => cx("flex min-h-10 items-center gap-2.5 rounded-lg px-2.5 text-sm", isActive ? "bg-accentsoft font-medium text-accent" : "hover:bg-surface2")}>
              <i.icon className="size-4" aria-hidden />{i.label}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-line bg-surface lg:flex">
        <div className="flex h-14 items-center gap-2 border-b border-line px-4 font-semibold"><span className="grid size-7 place-items-center rounded-lg bg-accent text-accentfg"><Navigation className="size-4" /></span>RoutePilot</div>
        {nav}
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button aria-label="Close menu" className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="relative flex h-full w-72 max-w-[85%] flex-col bg-surface">
            <div className="flex h-14 items-center justify-between border-b border-line px-4 font-semibold">RoutePilot<button aria-label="Close" onClick={() => setOpen(false)}><X className="size-5" /></button></div>
            {nav}
            <div className="border-t border-line p-3"><LanguageSwitch /></div>
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
          <button className="lg:hidden" aria-label="Open menu" onClick={() => setOpen(true)}><Menu className="size-5" /></button>
          {u.organizations.length > 1 && (
            <Select aria-label="Organization" className="max-w-56" value={u.active_organization_id ?? ""} onChange={(e) => { setOrg(e.target.value); void qc.clear(); }}>
              {u.organizations.map((o) => <option key={o.organization_id} value={o.organization_id}>{o.name}</option>)}
            </Select>
          )}
          {u.organizations.length === 1 && <span className="truncate text-sm font-medium">{u.organizations[0]?.name}</span>}
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden text-muted sm:inline">{u.full_name || u.email}{role ? ` · ${role.replace("_", " ")}` : u.is_platform_admin ? " · platform admin" : ""}</span>
            <span className="hidden sm:inline"><LanguageSwitch /></span>
            <button className="inline-flex min-h-10 items-center gap-1.5 rounded-lg px-2 hover:bg-surface2" onClick={() => void signOut().then(() => qc.clear())}><LogOut className="size-4" />{t("signOut")}</button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6"><div className="mx-auto max-w-7xl"><ErrorBoundary resetKey={loc.pathname}><Outlet /></ErrorBoundary></div></main>
      </div>
    </div>
  );
}
