import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Activity, CircleAlert, ClipboardCheck, Eye, ShieldCheck, Wrench } from "lucide-react";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { ROLE_PROFILES, roleProfile } from "../lib/auth-utils";
import type { UserRole } from "../types/api";

type LoginLocationState = { from?: string };

const PERSONA_ICONS = { operator: Eye, technician: Wrench, planner: ClipboardCheck, admin: ShieldCheck };

export function LoginScreen() {
  const { user, isLoading, login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectedRole, setSelectedRole] = useState<UserRole | null>(null);
  const requestedRoute = (location.state as LoginLocationState | null)?.from;

  if (user) return <Navigate replace to={requestedRoute ?? roleProfile(user.role).defaultRoute} />;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username || !password) {
      setError("Enter your username and password.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const signedInUser = await login(username, password);
      const profile = roleProfile(signedInUser.role);
      navigate(requestedRoute ?? profile.defaultRoute, { replace: true, state: { signedInAs: profile.label } });
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 401 ? "Invalid username or password." : "Sign-in is temporarily unavailable. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const selectPersona = (role: UserRole) => {
    setSelectedRole(role);
    setUsername(role);
    setPassword(import.meta.env.VITE_DEMO_PASSWORD ?? "");
    setError(null);
  };

  return (
    <main className="login-page">
      <section className="login-card login-card--personas" aria-labelledby="login-title">
        <div className="brand-lockup login-brand">
          <div className="brand-mark" aria-hidden="true"><Activity size={20} strokeWidth={1.8} /></div>
          <div><div className="brand-name">SENTINEL</div><div className="brand-subtitle">PREDICTIVE MAINTENANCE</div></div>
        </div>
        <div className="login-heading">
          <span className="eyebrow">Control room access</span>
          <h1 id="login-title">Choose your control-room role</h1>
          <p>Each persona opens a focused workspace with clear responsibilities and protected actions.</p>
        </div>
        <div className="persona-picker" role="group" aria-label="Choose a demo persona">
          {(Object.keys(ROLE_PROFILES) as UserRole[]).map((role) => {
            const profile = ROLE_PROFILES[role];
            const Icon = PERSONA_ICONS[role];
            const selected = selectedRole === role;
            return (
              <button className={`persona-card persona-card--${role}${selected ? " persona-card--selected" : ""}`} type="button" key={role} onClick={() => selectPersona(role)} aria-pressed={selected}>
                <span className="persona-icon" aria-hidden="true"><Icon size={18} strokeWidth={2} /></span>
                <span className="persona-card-copy"><strong>{profile.label}</strong><span>{profile.shortMission}</span></span>
                <span className="persona-card-access">{profile.capabilities[0]}</span>
              </button>
            );
          })}
        </div>
        <form className="login-form" onSubmit={submit} noValidate>
          <label htmlFor="username">Username</label>
          <input id="username" name="username" type="text" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required maxLength={64} />
          <label htmlFor="password">Password</label>
          <input id="password" name="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required maxLength={128} />
          {error && <div className="login-error" role="alert"><CircleAlert size={16} aria-hidden="true" /><span>{error}</span></div>}
          <button className="primary-button login-submit" type="submit" disabled={isLoading || submitting}>{submitting ? "Opening workspace…" : selectedRole ? `Enter as ${ROLE_PROFILES[selectedRole].label}` : "Sign in"}</button>
        </form>
        <p className="login-note">{import.meta.env.VITE_DEMO_PASSWORD ? "Demo personas use prefilled credentials." : "Choose a persona and enter your password."} Your selected role controls the workspace and actions available after sign-in.</p>
      </section>
    </main>
  );
}
