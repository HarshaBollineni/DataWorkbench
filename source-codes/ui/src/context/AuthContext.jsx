import { createContext, useContext, useEffect, useState } from "react";

import { getToken, login as apiLogin, logout as apiLogout, me } from "@/api/client";
import { bootstrapTheme } from "@/theme/useTheme";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // Only "loading" when there's a token to validate; otherwise we're done.
  const [loading, setLoading] = useState(() => !!getToken());

  // Restore session from a persisted token on first load.
  useEffect(() => {
    if (!getToken()) return;
    me()
      .then((r) => { setUser(r.user); bootstrapTheme(r.user?.theme); })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(username, password) {
    const u = await apiLogin(username, password);
    setUser(u);
    bootstrapTheme(u?.theme);
    return u;
  }

  async function logout() {
    await apiLogout();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, setUser, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
