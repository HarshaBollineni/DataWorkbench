import { Card, CardContent } from "@/components/ui/card";
import DangerZone from "@/pages/admin/DangerZone";
import UserManagement from "@/pages/admin/UserManagement";
import VersionHistory from "@/pages/admin/VersionHistory";

export default function Admin() {
  return <div className="max-w-none p-8">
    <h1 className="mb-1 text-2xl font-bold">Admin</h1>
    <p className="mb-6 text-sm text-slate-500">Platform administration — manage user accounts and reset the demo data.</p>
    <UserManagement />
    <Card><CardContent className="space-y-4 p-6"><h2 className="text-lg font-bold">Product data</h2><p className="text-sm text-slate-600">Uploaded assessments are retained across restarts. Re-upload an item only when its source file is unavailable.</p></CardContent></Card>
    <DangerZone />
    <Card className="mt-6"><CardContent className="p-6"><VersionHistory /></CardContent></Card>
  </div>;
}
