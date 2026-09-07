import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ShieldCheck } from "lucide-react";

import { BrandLogo } from "@/components/BrandLogo";
import { APP_VERSION } from "@/lib/appVersion";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/context/AuthContext";

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const [username, setUsername] = useState("anirban");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      const destination = typeof location.state?.from === "string"
        && location.state.from.startsWith("/")
        && !location.state.from.startsWith("/login")
        ? location.state.from
        : "/";
      navigate(destination, { replace: true });
    } catch (err) {
      setError(err.message?.includes("401") ? "Invalid username or password" : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-screen w-full items-center justify-center bg-dq-dark">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-2xl space-y-6"
      >
        <div className="space-y-4">
          <BrandLogo className="text-2xl" />
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-dq-purple rounded-lg text-dq-dark">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div>
              <h1 className="font-bold text-lg leading-tight">Aegis Labs</h1>
              <p className="text-xs text-slate-500">Trust your data before your models do.</p>
              <p className="text-[11px] text-slate-400">Archimedes {APP_VERSION}</p>
            </div>
          </div>
        </div>

        {location.state?.sessionExpired && !error && (
          <div className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2">
            Your session expired. Sign in again to continue where you left off.
          </div>
        )}

        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
            {error}
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="login-username" className="text-xs font-semibold text-slate-600">Username</Label>
          <Input id="login-username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="login-password" className="text-xs font-semibold text-slate-600">Password</Label>
          <Input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </div>

        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign In"}
        </Button>
      </form>
    </div>
  );
}
