import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/context/AuthContext";
import { useTour } from "@/context/TourContext";
import { useTheme } from "@/theme/useTheme";
import { THEMES, THEME_KEYS } from "@/theme/themes";
import { updateProfile } from "@/api/client";

const PERSONALITIES = ["professional", "concise", "friendly", "mentor"];

function Field({ label, children, hint }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs font-semibold text-slate-600">{label}</Label>
      {children}
      {hint && <p className="text-[11px] text-slate-400">{hint}</p>}
    </div>
  );
}

export default function Profile() {
  const { user, setUser } = useAuth();
  const { theme, setTheme } = useTheme(user?.theme);
  const { enabled: tourEnabled, setEnabled: setTourEnabled, setHasSeen } = useTour();
  const [pw, setPw] = useState({ current: "", next: "" });
  const [personality, setPersonality] = useState(user?.ai_personality || "professional");
  const [msg, setMsg] = useState(null);
  const [acct, setAcct] = useState({ name: user?.name || "", email: user?.email || "", function: user?.function || "" });
  const [acctMsg, setAcctMsg] = useState(null);

  if (!user) return null;

  async function saveAccount() {
    const r = await updateProfile(acct);
    setUser(r.user);
    setAcctMsg("Saved.");
  }

  async function savePersonality(value) {
    setPersonality(value);
    const r = await updateProfile({ ai_personality: value });
    setUser(r.user);
  }

  return (
    <div className="p-8 max-w-none">
      <h1 className="text-2xl font-bold mb-1">Profile</h1>
      <p className="text-sm text-slate-500 mb-6">Your account details and preferences.</p>

      <Card>
        <CardContent className="p-6 space-y-6">
          <h2 className="text-lg font-bold">Account</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Field label="Name">
              <Input value={acct.name} onChange={(e) => setAcct({ ...acct, name: e.target.value })} />
            </Field>
            <Field label="Email">
              <Input value={acct.email} onChange={(e) => setAcct({ ...acct, email: e.target.value })} />
            </Field>
            <Field label="Function">
              <Input value={acct.function} onChange={(e) => setAcct({ ...acct, function: e.target.value })} />
            </Field>
            <Field label="Role">
              <Input value={user.role ?? ""} readOnly className="bg-slate-50" />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={saveAccount}>Save account</Button>
            {acctMsg && <span className="text-sm text-green-700">{acctMsg}</span>}
          </div>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardContent className="p-6 space-y-6">
          <h2 className="text-lg font-bold">Change password</h2>
          {msg && <div className="text-sm text-green-700">{msg}</div>}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Field label="Current password">
              <Input type="password" value={pw.current}
                onChange={(e) => setPw({ ...pw, current: e.target.value })} />
            </Field>
            <Field label="New password">
              <Input type="password" value={pw.next}
                onChange={(e) => setPw({ ...pw, next: e.target.value })} />
            </Field>
          </div>
          <Button variant="outline" onClick={() => { setMsg("Password updated (demo)."); setPw({ current: "", next: "" }); }}>
            Update password
          </Button>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardContent className="p-6 space-y-6">
          <h2 className="text-lg font-bold">Preferences</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Field label="Theme" hint="Colour only — contrast stays AA-legible">
              <Select value={theme} onValueChange={setTheme}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {THEME_KEYS.map((k) => <SelectItem key={k} value={k}>{THEMES[k].label}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="AI personality" hint="Feeds agent salutations">
              <Select value={personality} onValueChange={savePersonality}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PERSONALITIES.map((p) => (
                    <SelectItem key={p} value={p} className="capitalize">{p}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-slate-100 p-4">
            <div>
              <div className="text-sm font-semibold">Enable Guided Product Tour</div>
              <div className="text-xs text-slate-400">
                Replays the spotlight walkthrough of the agentic workflow.
              </div>
            </div>
            <Switch
              checked={tourEnabled}
              onCheckedChange={(v) => { setTourEnabled(v); if (v) setHasSeen(false); }}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
