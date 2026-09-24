import { createContext, useContext, useEffect, useRef, useState } from "react";

import { getToken, login as apiLogin, logout as apiLogout, me } from "@/api/client";
import { SESSION_EXPIRED_EVENT } from "@/api/transport";
import { bootstrapTheme } from "@/theme/useTheme";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [sessionExpired, setSessionExpired] = useState(false);
  // Only "loading" when there's a token to validate; otherwise we're done.
  const [loading, setLoading] = useState(() => !!getToken());
  const restoreRequest = useRef(null);

  // Keep the auth shell in sync when any API call discovers that a session
  // expired while the application was already open.
  useEffect(() => {
    const handleSessionExpired = () => {
      setSessionExpired(true);
      setUser(null);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
  }, []);

  // Restore session from a persisted token on first load.
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    let current = true;
    // Reuse only this mount's request during StrictMode's effect replay.
    // No session data is cached globally or reused across credentials.
    if (restoreRequest.current?.token !== token) restoreRequest.current = { token, promise: me() };
    restoreRequest.current.promise
      .then((r) => { if (current && getToken() === token) { setUser(r.user); bootstrapTheme(r.user?.theme); } })
      .catch(() => { if (current && getToken() === token) setUser(null); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, []);

  async function login(username, password) {
    const u = await apiLogin(username, password);
    setUser(u);
    setSessionExpired(false);
    bootstrapTheme(u?.theme);
    return u;
  }

  async function logout() {
    await apiLogout();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, setUser, sessionExpired, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
