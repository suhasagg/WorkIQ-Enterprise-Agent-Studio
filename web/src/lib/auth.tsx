"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export type User = { id: string; email: string; name: string; role: string; workspace_id: string; workspace_name: string; scopes: string[] };
type Ctx = { user: User | null; loading: boolean; can: (scope: string) => boolean; login: (email: string, password: string) => Promise<void>; logout: () => Promise<void> };

const AuthContext = createContext<Ctx>({ user: null, loading: true, can: () => false, login: async () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<User>("/api/auth/me")
      .then(setUser)
      .catch((e) => {
        if (!(e instanceof ApiError)) console.error(e);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api<{ user: User }>("/api/auth/login", { method: "POST", json: { email, password } });
    setUser(res.user);
  }, []);
  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST", json: {} });
    setUser(null);
  }, []);
  const can = useCallback((scope: string) => !!user?.scopes.includes(scope), [user]);

  return <AuthContext.Provider value={{ user, loading, can, login, logout }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
