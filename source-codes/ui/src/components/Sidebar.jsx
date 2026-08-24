import { useState } from "react"
import { NavLink } from "react-router-dom"
import {
  Beaker, BookOpen, ChevronLeft, ChevronRight, Database, Layers,
  LogOut, Shield, ShieldCheck, Ticket, UploadCloud, User,
} from "lucide-react"

import { BrandLogo } from "@/components/BrandLogo"
import { APP_VERSION } from "@/lib/appVersion"
import { cn } from "@/lib/utils"
import { useAuth } from "@/context/AuthContext"

const navItems = [
  { to: "/", label: "Data Inventory", icon: Database, end: true, cls: "tour-sidebar" },
  { to: "/data-sourcing", label: "Data Sourcing", icon: UploadCloud, cls: "tour-data-sourcing" },
  { to: "/test-lab", label: "Test Lab", icon: Beaker },
  { to: "/issues", label: "Issue Management", icon: Ticket },
  { to: "/knowledge-base", label: "Knowledge Base", icon: BookOpen },
  { to: "/dq-framework", label: "DQ Framework", icon: Layers },
  { to: "/admin", label: "Admin", icon: Shield },
]

function initials(name) {
  if (!name) return "?"
  return name.split(" ").map((part) => part[0]).slice(0, 2).join("").toUpperCase()
}

function Sidebar() {
  const { user, logout } = useAuth()
  const isAdmin = (user?.authz_roles || []).includes("admin")
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("sidebarCollapsed") === "true"
  )
  const [hoverExpanded, setHoverExpanded] = useState(false)
  const compact = collapsed && !hoverExpanded

  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    setHoverExpanded(false)
    localStorage.setItem("sidebarCollapsed", String(next))
  }

  return (
    <aside
      className={cn(
        "relative flex h-screen shrink-0 flex-col justify-between bg-dq-dark text-white transition-[width] duration-200",
        compact ? "w-20" : "w-64"
      )}
      data-collapsed={compact}
      onMouseEnter={() => collapsed && setHoverExpanded(true)}
      onMouseLeave={() => setHoverExpanded(false)}
    >
      <button
        type="button"
        onClick={toggleCollapsed}
        className="absolute -right-3 top-5 z-20 flex h-7 w-7 items-center justify-center rounded-full border border-slate-600 bg-dq-dark text-slate-300 shadow-sm hover:text-white"
        aria-label={collapsed ? "Keep navigation expanded" : "Collapse navigation"}
        title={collapsed ? "Keep navigation expanded" : "Collapse navigation"}
      >
        {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
      </button>

      <div>
        <div className={cn("border-b border-slate-700/60", compact ? "p-4" : "space-y-2 p-6")}>
          {!compact && <BrandLogo className="text-xl" dark />}
          <div className={cn("flex items-center", compact ? "justify-center" : "gap-2")}>
            <div className="rounded-md bg-dq-purple p-1.5 text-dq-dark">
              <ShieldCheck className="h-4 w-4" />
            </div>
            {!compact && (
              <div>
                <h2 className="text-sm font-bold leading-tight tracking-wide">Aegis Labs</h2>
                <p className="text-[10px] leading-tight text-slate-400">Trust your data before your models do.</p>
                <p className="text-[10px] leading-tight text-slate-500">Archimedes {APP_VERSION}</p>
              </div>
            )}
          </div>
        </div>

        <nav className={cn("space-y-1", compact ? "p-3" : "p-4")} aria-label="Primary navigation">
          {navItems.filter((item) => item.to !== "/admin" || isAdmin).map(({ to, label, icon: Icon, end, cls }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={compact ? label : undefined}
              aria-label={compact ? label : undefined}
              className={({ isActive }) => cn(
                cls,
                "flex items-center rounded-lg text-sm font-medium transition-colors",
                compact ? "justify-center px-2 py-3" : "gap-3 px-3 py-2.5",
                isActive
                  ? "bg-dq-purple text-dq-dark"
                  : "text-slate-400 hover:bg-slate-800 hover:text-white"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!compact && label}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="border-t border-slate-700/60">
        <NavLink
          to="/profile"
          title={compact ? `${user?.name ?? "Profile"} · Profile` : undefined}
          aria-label={compact ? "Open profile" : undefined}
          className={({ isActive }) => cn(
            "flex items-center transition-colors",
            compact ? "justify-center p-3" : "gap-3 p-4",
            isActive ? "bg-slate-800" : "hover:bg-slate-800"
          )}
        >
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-dq-purple text-xs font-bold text-dq-dark">
            {initials(user?.name)}
          </div>
          {!compact && (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">{user?.name ?? "—"}</div>
              <div className="truncate text-xs text-slate-400">{user?.role ?? ""}</div>
            </div>
          )}
          {!compact && <User className="ml-auto h-4 w-4 text-slate-500" />}
        </NavLink>
        <button
          type="button"
          onClick={logout}
          title={compact ? "Sign out" : undefined}
          aria-label={compact ? "Sign out" : undefined}
          className={cn(
            "flex w-full items-center border-t border-slate-700/60 px-4 py-3 text-sm font-medium text-slate-400 transition-colors hover:bg-slate-800 hover:text-white",
            compact ? "justify-center" : "gap-3"
          )}
        >
          <LogOut className="h-4 w-4" />
          {!compact && "Sign out"}
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
