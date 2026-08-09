import { useCallback, useMemo, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { CurrentUserResponse, LoginResponse, UserRole } from "../types/api";
import { fetchJson } from "./api";
import { AUTH_QUERY_KEY, AuthContext, type AuthContextValue, type AuthUser } from "./auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const session = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: () => fetchJson<AuthUser>("/auth/me"),
    retry: false,
    staleTime: 5 * 60_000,
  });

  const login = useCallback(async (username: string, password: string): Promise<AuthUser> => {
    const response = await fetchJson<LoginResponse>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const user: AuthUser = { username: response.username, role: response.role, can_switch_persona: response.can_switch_persona };
    queryClient.setQueryData(AUTH_QUERY_KEY, user);
    return user;
  }, [queryClient]);

  const switchPersona = useCallback(async (role: UserRole): Promise<AuthUser> => {
    const user = await fetchJson<CurrentUserResponse>("/auth/persona", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    queryClient.setQueryData(AUTH_QUERY_KEY, user);
    // Data and mutation permissions are role-dependent; do not carry one
    // persona's cached workspace into another persona's presentation.
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
    return user;
  }, [queryClient]);

  const logout = useCallback(async (): Promise<void> => {
    try {
      await fetchJson<{ message: string }>("/auth/logout", { method: "POST" });
    } finally {
      queryClient.setQueryData<AuthUser | null>(AUTH_QUERY_KEY, null);
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
    }
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(() => ({
    user: session.data ?? null,
    isLoading: session.isPending,
    login,
    switchPersona,
    logout,
  }), [login, logout, session.data, session.isPending, switchPersona]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
