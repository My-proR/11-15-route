import { Suspense, lazy, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes as RRoutes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useSession } from "./lib/session";
import { useMe } from "./lib/hooks";
import AppShell from "./layout/AppShell";
import Login from "./pages/Login";
import Join from "./pages/Join";
import Invites from "./pages/Invites";
import Billing, { InvoiceView } from "./pages/Billing";
import PlatformBilling from "./pages/PlatformBilling";
import InstallPrompt from "./components/InstallPrompt";
import { Drivers, Employees, Locations, Shifts, Vehicles } from "./pages/MasterData";
import Allocations from "./pages/Allocations";
import { Exceptions, LeavePage, Overview, VehicleBoard } from "./pages/Operations";
import Fleet from "./pages/Fleet";
const LiveTracking = lazy(() => import("./pages/Safety").then((m) => ({ default: m.LiveTracking })));
const SosIncidents = lazy(() => import("./pages/Safety").then((m) => ({ default: m.SosIncidents })));
import { AuditLogs, Flags, Health, Members, Notifications, Organizations, Reports, SettingsPage } from "./pages/Admin";
import Portal, { SosPage } from "./pages/Portal";
import ImportEmployees from "./pages/ImportEmployees";
import RoutesPage from "./pages/Routes";
import { Spinner, Toaster } from "./components/ui";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 } } });

function Home() {
  const { data: me } = useMe();
  if (!me) return null;
  if (me.role === "driver" || me.role === "employee") return <Navigate to="/me" replace />;
  if (!me.role && me.is_platform_admin) return <Navigate to="/platform/organizations" replace />;
  return <Overview />;
}

function Gate() {
  const { token, ready, init } = useSession();
  useEffect(() => { void init(); }, [init]);
  if (!ready) return <Spinner />;
  return (
    <RRoutes>
      <Route path="join/:token" element={<Join />} />
      <Route path="*" element={token ? <Authed /> : <Login />} />
    </RRoutes>
  );
}

function Authed() {
  return (
    <RRoutes>
      <Route element={<AppShell />}>
        <Route index element={<Home />} />
        <Route path="allocations" element={<Allocations />} />
        <Route path="exceptions" element={<Exceptions />} />
        <Route path="vehicle-board" element={<VehicleBoard />} />
        <Route path="fleet" element={<Fleet />} />
        <Route path="tracking" element={<Suspense fallback={<Spinner />}><LiveTracking /></Suspense>} />
        <Route path="sos" element={<Suspense fallback={<Spinner />}><SosIncidents /></Suspense>} />
        <Route path="leave" element={<LeavePage />} />
        <Route path="employees" element={<Employees />} />
        <Route path="employees/import" element={<ImportEmployees />} />
        <Route path="drivers" element={<Drivers />} />
        <Route path="vehicles" element={<Vehicles />} />
        <Route path="locations" element={<Locations />} />
        <Route path="shifts" element={<Shifts />} />
        <Route path="routes" element={<RoutesPage />} />
        <Route path="reports" element={<Reports />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="audit" element={<AuditLogs />} />
        <Route path="members" element={<Members />} />
        <Route path="invites" element={<Invites />} />
        <Route path="billing" element={<Billing />} />
        <Route path="billing/invoices/:id" element={<InvoiceView />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="me" element={<Portal />} />
        <Route path="me/sos" element={<SosPage />} />
        <Route path="platform/organizations" element={<Organizations />} />
        <Route path="platform/billing" element={<PlatformBilling />} />
        <Route path="platform/flags" element={<Flags />} />
        <Route path="platform/health" element={<Health />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </RRoutes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><Gate /></BrowserRouter>
      <Toaster />
      <InstallPrompt />
    </QueryClientProvider>
  );
}
