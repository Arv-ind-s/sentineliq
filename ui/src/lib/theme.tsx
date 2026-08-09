import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { THEME_STORAGE_KEY, ThemeContext, type ResolvedTheme, type ThemeContextValue, type ThemePreference } from "./theme-context";

/** The page CSP forbids inline scripts, so the first paint cannot be stamped
 *  before React mounts. `tokens.css` therefore ships dark as the default and a
 *  `prefers-color-scheme` block for the unstamped case, which means an
 *  unstamped viewer already sees the correct theme and this provider only has
 *  to write the attribute when a stored preference overrides the OS. */
function readStoredPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Private-mode storage denial is not an error worth surfacing; fall through.
  }
  return "system";
}

function systemTheme(): ResolvedTheme {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [systemResolved, setSystemResolved] = useState<ResolvedTheme>(systemTheme);
  const firstRun = useRef(true);

  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = (event: MediaQueryListEvent) => setSystemResolved(event.matches ? "light" : "dark");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const theme: ResolvedTheme = preference === "system" ? systemResolved : preference;

  useEffect(() => {
    const root = document.documentElement;
    if (preference === "system") root.removeAttribute("data-theme");
    else root.dataset.theme = preference;
    root.style.colorScheme = theme;

    // Suppress the cross-fade on mount so a stored preference does not animate
    // the whole page from the default theme on every reload.
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    root.classList.add("theme-switching");
    const timer = window.setTimeout(() => root.classList.remove("theme-switching"), 200);
    return () => window.clearTimeout(timer);
  }, [preference, theme]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // A denied write only costs persistence across reloads, not this session.
    }
  }, []);

  // Toggling from `system` commits to the opposite of what is on screen, which
  // is what a viewer means when they click a switch showing the current theme.
  const toggle = useCallback(() => setPreference(theme === "dark" ? "light" : "dark"), [setPreference, theme]);

  const value = useMemo<ThemeContextValue>(() => ({ preference, theme, setPreference, toggle }), [preference, setPreference, theme, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
