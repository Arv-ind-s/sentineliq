import { createContext, useContext } from "react";

/** What the viewer chose. `system` defers to the OS setting and keeps tracking it. */
export type ThemePreference = "light" | "dark" | "system";
/** What is actually painted once `system` has been resolved. */
export type ResolvedTheme = "light" | "dark";

export type ThemeContextValue = {
  preference: ThemePreference;
  theme: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  toggle: () => void;
};

export const THEME_STORAGE_KEY = "sentinel.theme";
export const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside ThemeProvider");
  return context;
}
