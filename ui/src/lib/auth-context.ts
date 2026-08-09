import { createContext, useContext } from "react";
import type { CurrentUserResponse, UserRole } from "../types/api";

export type AuthUser = CurrentUserResponse;

export type AuthContextValue = {
  user: AuthUser | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<AuthUser>;
  switchPersona: (role: UserRole) => Promise<AuthUser>;
  logout: () => Promise<void>;
};

export const AUTH_QUERY_KEY = ["auth", "me"] as const;
export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
