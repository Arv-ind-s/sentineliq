import { Activity, CalendarDays, ChartNoAxesCombined, ClipboardCheck, Database, Eye, Factory, FileCheck2, LogOut, Radio, Search, ShieldCheck, UserRound, Wrench } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { LayoutGroup, MotionConfig } from "framer-motion";
import type { HealthResponse, MetaResponse } from "./types/api";
import type { UserRole } from "./types/api";
import { MonoNumber, Skeleton, StatusPill } from "./components/primitives";
import { CommandPalette } from "./components/CommandPalette";
import { ThemeToggle } from "./components/ThemeToggle";
import { ToastProvider } from "./components/Toaster";
import { Tooltip } from "./components/Tooltip";
import { fetchJson } from "./lib/api";
import { AuthProvider } from "./lib/auth";
import { useAuth } from "./lib/auth-context";
import { ROLE_PROFILES, roleProfile } from "./lib/auth-utils";
import { LiveStreamProvider } from "./lib/stream";
import { useLiveStream } from "./lib/stream-context";
import { ThemeProvider } from "./lib/theme";
import { LoginScreen } from "./screens/LoginScreen";
import { DiagnosisScreen, FloorScreen, LedgerScreen, MachineDetailScreen, ScheduleScreen, TrustScreen } from "./screens/screens";
import { AuditScreen } from "./screens/AuditScreen";
import { ERPScreen } from "./screens/ERPScreen";

type NavigationItem = { to: string; label: string; icon: typeof Factory; roles: readonly UserRole[] };
const ALL_ROLES: readonly UserRole[] = ["operator", "technician", "planner", "admin"];
const navigation: readonly NavigationItem[] = [
  { to: "/floor", label: "Live Floor", icon: Factory, roles: ALL_ROLES },
  { to: "/schedule", label: "Maintenance", icon: CalendarDays, roles: ["technician", "planner", "admin"] },
  { to: "/erp", label: "ERP Work Orders", icon: Database, roles: ["technician", "planner", "admin"] },
  { to: "/ledger", label: "Downtime Ledger", icon: ChartNoAxesCombined, roles: ["planner", "admin"] },
  { to: "/trust", label: "Trust & Evals", icon: ShieldCheck, roles: ["admin"] },
  { to: "/audit", label: "Audit Ledger", icon: FileCheck2, roles: ["planner", "admin"] },
];

const HEALTH_DEPENDENCIES = ["db", "tsdb", "models", "erp", "llm", "stream"];

function useMeta() {
  return useQuery({
    queryKey: ["meta"],
    queryFn: () => fetchJson<MetaResponse>("/meta"),
    staleTime: 60_000,
  });
}

function DependencyDot({ name, dependency }: { name: string; dependency: HealthResponse["dependencies"][number] | undefined }) {
  const label = dependency?.status ?? "unavailable";
  const status = dependency?.status ?? "down";
  return (
    <Tooltip
      placement="bottom"
      content={
        <>
          <b>{name}</b>
          <span>{dependency ? `${dependency.status} · breaker ${dependency.breaker_state}` : "unavailable"}</span>
          <span>{dependency?.detail ?? "Dependency health could not be read."}</span>
          {dependency?.latency_ms != null && <span>{dependency.latency_ms.toFixed(2)} ms</span>}
        </>
      }
    >
      <span className="dependency" tabIndex={0} role="img" aria-label={`${name} dependency ${status}`}>
        <span className={`dependency-dot dependency-dot--${status}`} aria-hidden="true" />
        <span className="dependency-name">{name}</span>
        <span className="dependency-status">{label}</span>
      </span>
    </Tooltip>
  );
}

function HealthStrip({ onOpenPalette }: { onOpenPalette: () => void }) {
  const stream = useLiveStream();
  const { user, logout, switchPersona } = useAuth();
  const navigate = useNavigate();
  const [switchingPersona, setSwitchingPersona] = useState(false);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => fetchJson<HealthResponse>("/health"),
    refetchInterval: 15_000,
  });
  const meta = useMeta();

  const degraded = (health.data?.dependencies ?? []).filter((dependency) => dependency.status !== "up").length;
  const personaIcons = { operator: Eye, technician: Wrench, planner: ClipboardCheck, admin: ShieldCheck };
  const profile = user ? roleProfile(user.role) : null;
  const PersonaIcon = user ? personaIcons[user.role] : UserRound;

  const changePersona = async (role: UserRole) => {
    if (!user || role === user.role) return;
    setSwitchingPersona(true);
    try {
      await switchPersona(role);
      navigate(roleProfile(role).defaultRoute, { replace: true });
    } finally {
      setSwitchingPersona(false);
    }
  };

  return (
    <header className="app-topbar" aria-label="System status">
      <div className="topbar-status">
        {health.isPending
          ? <Skeleton width="96px" height="24px" />
          : health.isError
            ? <StatusPill status="NODATA" label="No data" />
            : <StatusPill status={health.data?.status === "healthy" ? "LOW" : health.data?.status === "degraded" ? "MEDIUM" : "CRITICAL"} label={health.data?.status ?? "No data"} />}
        {!health.isPending && !health.isError && (
          <span className="topbar-status-note">{degraded === 0 ? "all dependencies up" : `${degraded} degraded`}</span>
        )}
      </div>

      <div className="topbar-dependencies">
        {health.isPending ? HEALTH_DEPENDENCIES.map((name) => <Skeleton width="110px" height="12px" key={name} />)
          : HEALTH_DEPENDENCIES.map((name) => (
              <DependencyDot key={name} name={name} dependency={health.isError ? undefined : health.data?.dependencies.find((item) => item.name === name)} />
            ))}
      </div>

      <div className="topbar-actions">
        <button className="topbar-search" type="button" onClick={onOpenPalette} aria-label="Open command palette">
          <Search size={14} aria-hidden="true" />
          <span>Search…</span>
          <kbd className="palette-kbd">Ctrl K</kbd>
        </button>

        <Tooltip placement="bottom" content={<><b>Live replay stream</b><span>{stream.state}</span><span>model {meta.data?.versions.model ?? "pending"}</span><span>replay {health.data?.replay ?? "pending"}</span></>}>
          <span className={`stream-chip stream-chip--${stream.state}`} tabIndex={0}>
            <Radio size={12} aria-hidden="true" />
            {stream.state === "connected" ? "Live" : stream.state === "connecting" ? "Connecting…" : "Reconnecting…"}
          </span>
        </Tooltip>

        <ThemeToggle />

        {user && (
          <div className={`topbar-user topbar-user--${user.role}`} title={`Signed in as ${profile?.label}`}>
            {user.can_switch_persona && (
              <label className="persona-switcher">
                <span>Demo as</span>
                <select aria-label="Switch active persona" value={user.role} disabled={switchingPersona} onChange={(event) => { void changePersona(event.target.value as UserRole); }}>
                  {(Object.keys(ROLE_PROFILES) as UserRole[]).map((role) => <option key={role} value={role}>{ROLE_PROFILES[role].label}</option>)}
                </select>
              </label>
            )}
            <span className="topbar-role-icon" aria-hidden="true"><PersonaIcon size={15} strokeWidth={2} /></span>
            <span className="topbar-user-identity">
              <MonoNumber>{user.username}</MonoNumber>
              <span>{profile?.shortMission}</span>
            </span>
            <Tooltip placement="bottom" content="Sign out">
              <button className="icon-button" type="button" onClick={() => { void logout(); }} aria-label="Log out">
                <LogOut size={15} aria-hidden="true" />
              </button>
            </Tooltip>
          </div>
        )}
      </div>
    </header>
  );
}

function RoleConsole() {
  const { user } = useAuth();
  if (!user) return null;
  const profile = roleProfile(user.role);
  const personaIcons = { operator: Eye, technician: Wrench, planner: ClipboardCheck, admin: ShieldCheck };
  const PersonaIcon = personaIcons[user.role];
  return (
    <section className={`role-console role-console--${user.role}`} aria-labelledby="role-console-title">
      <div className="role-console-heading"><span className="role-console-icon" aria-hidden="true"><PersonaIcon size={17} strokeWidth={2} /></span><span className="eyebrow">Your role</span></div>
      <h2 id="role-console-title">{profile.workspace}</h2>
      <p>{profile.mission}</p>
      {user.can_switch_persona && <span className="role-console-switchable">Admin demo session · persona switcher available</span>}
      <span className="role-console-label">You can</span>
      <ul>{profile.capabilities.map((capability) => <li key={capability}>{capability}</li>)}</ul>
    </section>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const visibleNavigation = navigation.filter((item) => user && item.roles.includes(user.role));
  const [paletteOpen, setPaletteOpen] = useState(false);

  return (
    <div className={`app-shell${user ? ` app-shell--${user.role}` : ""}`}>
      <aside className="app-sidebar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><Activity size={18} strokeWidth={2} /></div>
          <div>
            <div className="brand-name">SENTINEL</div>
            <div className="brand-subtitle">PREDICTIVE MAINTENANCE</div>
          </div>
        </div>
        <div className="sidebar-label">Control room</div>
        <nav className="primary-nav" aria-label="Primary navigation">
          {visibleNavigation.map(({ to, label, icon: Icon }) => (
            <NavLink className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`} key={to} to={to}>
              <Icon size={17} strokeWidth={1.9} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <RoleConsole />
        <div className="sidebar-footer">
          <span className="eyebrow">Environment</span>
          <MonoNumber>DEMO / SYNTHETIC</MonoNumber>
          <span className="muted">All values are traceable synthetic data.</span>
        </div>
      </aside>
      <main className="app-main">
        <HealthStrip onOpenPalette={() => setPaletteOpen(true)} />
        <div className="content-outlet" id="main-content" tabIndex={-1}>{children}</div>
      </main>
      <CommandPalette navigation={navigation} open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}

function AuthLoadingScreen() {
  return <main className="login-page"><section className="login-card" aria-live="polite"><span className="eyebrow">Control room access</span><h1 className="auth-loading-title">Checking session…</h1><p className="login-note">Restoring the secure browser session.</p></section></main>;
}

function RequireAuth() {
  const { user, isLoading } = useAuth();
  const location = useLocation();
  if (isLoading) return <AuthLoadingScreen />;
  if (!user) return <Navigate replace to="/login" state={{ from: `${location.pathname}${location.search}` }} />;
  return <Outlet />;
}

function RequireRole({ roles, children }: { roles: readonly UserRole[]; children: ReactNode }) {
  const { user } = useAuth();
  if (!user || !roles.includes(user.role)) return <Navigate replace to={user ? roleProfile(user.role).defaultRoute : "/login"} />;
  return <>{children}</>;
}

function ProtectedShell() {
  return <LiveStreamProvider><Shell><Routes>
    <Route index element={<Navigate replace to="/floor" />} />
    <Route path="/floor" element={<FloorScreen />} />
    <Route path="/machines/:machineId" element={<MachineDetailScreen />} />
    <Route path="/diagnosis/:diagnosisId" element={<DiagnosisScreen />} />
    <Route path="/schedule" element={<RequireRole roles={["technician", "planner", "admin"]}><ScheduleRoute /></RequireRole>} />
    <Route path="/erp" element={<RequireRole roles={["technician", "planner", "admin"]}><ERPScreen /></RequireRole>} />
    <Route path="/ledger" element={<RequireRole roles={["planner", "admin"]}><LedgerScreen /></RequireRole>} />
    <Route path="/trust" element={<RequireRole roles={["admin"]}><TrustScreen /></RequireRole>} />
    <Route path="/audit" element={<RequireRole roles={["planner", "admin"]}><AuditScreen /></RequireRole>} />
    <Route path="*" element={<Navigate replace to="/floor" />} />
  </Routes></Shell></LiveStreamProvider>;
}

function ScheduleRoute() {
  const { user } = useAuth();
  return <ScheduleScreen currentRole={user?.role} />;
}

/** Route changes move focus back to the document so screen-reader and keyboard
 *  users start at the new screen rather than wherever the old link was. */
function useRouteFocus() {
  const location = useLocation();
  useEffect(() => {
    document.querySelector<HTMLElement>(".content-outlet")?.focus({ preventScroll: true });
  }, [location.pathname]);
}

function RouteFocus() {
  useRouteFocus();
  return null;
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <ToastProvider>
          <MotionConfig reducedMotion="user">
            <LayoutGroup id="machine-navigation">
              <RouteFocus />
              <Routes>
                <Route path="/login" element={<LoginScreen />} />
                <Route element={<RequireAuth />}>
                  <Route path="/*" element={<ProtectedShell />} />
                </Route>
              </Routes>
            </LayoutGroup>
          </MotionConfig>
        </ToastProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
