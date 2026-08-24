import { useEffect, useState } from "react";
import { AlertTriangle, Trash2, UserPlus } from "lucide-react";

import { createUser, deleteUser, listUsers } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuth } from "@/context/AuthContext";

const EMPTY_FORM = { username: "", password: "", name: "", email: "", function: "", role: "" };

export default function UserManagement() {
  const { user } = useAuth();
  const [error, setError] = useState(null);
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [authzUser, setAuthzUser] = useState(true);
  const [authzAdmin, setAuthzAdmin] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);

  async function refreshUsers() {
    try { setUsers(await listUsers()); } catch (reason) { setError(reason.message); }
  }
  useEffect(() => { listUsers().then(setUsers).catch((reason) => setError(reason.message)); }, []);

  async function create(event) {
    event.preventDefault();
    setCreating(true);
    setError(null);
    const authz_roles = [];
    if (authzUser) authz_roles.push("user");
    if (authzAdmin) authz_roles.push("admin");
    try {
      await createUser({ ...form, authz_roles });
      setForm(EMPTY_FORM);
      setAuthzUser(true);
      setAuthzAdmin(false);
      await refreshUsers();
    } catch (reason) { setError(reason.message); } finally { setCreating(false); }
  }

  async function remove(username) {
    setError(null);
    try { await deleteUser(username); await refreshUsers(); } catch (reason) { setError(reason.message); } finally { setDeleteTarget(null); }
  }

  const setField = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));
  return <>
    {error && <div className="mb-6 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}
    <Card className="mb-6"><CardContent className="space-y-5 p-6">
      <h2 className="text-lg font-bold">User management</h2>
      <Table><TableHeader><TableRow><TableHead>Username</TableHead><TableHead>Name</TableHead><TableHead>Email</TableHead><TableHead>Role</TableHead><TableHead>Authorization</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
        <TableBody>{users.map((entry) => <TableRow key={entry.username}><TableCell className="font-medium">{entry.username}</TableCell><TableCell>{entry.name}</TableCell><TableCell>{entry.email}</TableCell><TableCell>{entry.role}</TableCell><TableCell><div className="flex flex-wrap gap-1">{(entry.authz_roles || []).map((role) => <Badge key={role} variant="secondary">{role}</Badge>)}</div></TableCell><TableCell className="text-right">{entry.username !== user?.username && <Button variant="ghost" size="sm" className="text-red-600 hover:text-red-700" onClick={() => setDeleteTarget(entry.username)}><Trash2 className="h-4 w-4" /></Button>}</TableCell></TableRow>)}
          {!users.length && <TableRow><TableCell colSpan={6} className="text-center text-slate-400">No users.</TableCell></TableRow>}
        </TableBody>
      </Table>
      <form onSubmit={create} className="space-y-4 border-t border-slate-100 pt-5">
        <h3 className="text-sm font-semibold text-slate-700">Create user</h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {[['username', 'Username'], ['password', 'Password'], ['name', 'Name'], ['email', 'Email'], ['function', 'Function'], ['role', 'Role']].map(([key, title]) => <div key={key} className="space-y-1.5"><Label className="text-xs font-semibold text-slate-600">{title}</Label><Input type={key === "password" ? "password" : key === "email" ? "email" : undefined} value={form[key]} onChange={setField(key)} required={["username", "password"].includes(key)} /></div>)}
        </div>
        <div className="space-y-1.5"><Label className="text-xs font-semibold text-slate-600">Authorization</Label><div className="flex items-center gap-6"><label className="flex items-center gap-2 text-sm"><Checkbox checked={authzUser} onCheckedChange={(value) => setAuthzUser(!!value)} />user</label><label className="flex items-center gap-2 text-sm"><Checkbox checked={authzAdmin} onCheckedChange={(value) => setAuthzAdmin(!!value)} />admin</label></div></div>
        <Button type="submit" disabled={creating}><UserPlus className="h-4 w-4" />{creating ? "Creating…" : "Create user"}</Button>
      </form>
    </CardContent></Card>
    <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}><DialogContent><DialogHeader><DialogTitle className="flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-amber-500" /> Delete user?</DialogTitle><DialogDescription>Permanently delete the account &quot;{deleteTarget}&quot;. This cannot be undone.</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setDeleteTarget(null)}>Cancel</Button><Button variant="destructive" onClick={() => remove(deleteTarget)}>Delete</Button></DialogFooter></DialogContent></Dialog>
  </>;
}
