import { Outlet } from 'react-router-dom'
import { NavItem, Button } from '@/components'
import { useAuth } from '@/context/AuthContext'
import {
  BiBuildingHouse,
  BiGridAlt,
  BiHomeAlt,
  BiKey,
  BiListUl,
  BiSearch,
  BiShieldAlt2,
  BiUpload,
  BiUser,
  BiCog,
} from 'react-icons/bi'

/** layout.containers.AppShell + Sidebar + Main from design.json */
const appShellClass = 'min-h-screen bg-app text-text-primary'
const sidebarClass =
  'fixed inset-y-0 left-0 w-72 bg-[#0f1f2b] text-white border-r border-white/10 px-6 py-6 flex flex-col overflow-y-auto shadow-lg'
const mainClass = 'ml-72 min-h-screen flex flex-col'
const topBarClass = 'h-20 px-10 flex items-center justify-between gap-6'

export function AppShell() {
  const { user, logout, loginRequired } = useAuth()
  return (
    <div className={appShellClass}>
      <aside className={sidebarClass}>
        <nav className="flex flex-col gap-1 mt-1" aria-label="Primary">
          <NavItem to="/dashboard" icon={<BiGridAlt className="h-5 w-5" />}>
            Dashboard
          </NavItem>
          <NavItem to="/" icon={<BiHomeAlt className="h-5 w-5" />}>
            Screening
          </NavItem>
          <NavItem to="/search" icon={<BiSearch className="h-5 w-5" />}>
            Search database
          </NavItem>
          <NavItem to="/review" icon={<BiShieldAlt2 className="h-5 w-5" />}>
            Match review
          </NavItem>
          <NavItem to="/companies-house" icon={<BiBuildingHouse className="h-5 w-5" />}>
            Companies House
          </NavItem>
          {(!loginRequired || user?.is_admin) && (
            <NavItem to="/admin" icon={<BiCog className="h-5 w-5" />}>
              Admin
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/users" icon={<BiUser className="h-5 w-5" />}>
              Users
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/bulk-screening" icon={<BiUpload className="h-5 w-5" />}>
              Bulk screening
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/jobs" icon={<BiListUl className="h-5 w-5" />}>
              Screening jobs
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/api-keys" icon={<BiKey className="h-5 w-5" />}>
              API keys
            </NavItem>
          )}
        </nav>
      </aside>
      <main className={mainClass}>
        <header className={topBarClass}>
          <div>
            <h1 className="text-2xl font-semibold text-text-primary">Sanctions & PEP Screening</h1>
          </div>
          <div className="flex items-center gap-4">
            <span className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs text-text-secondary">
              {user?.username}
            </span>
            <Button variant="ghost" size="sm" onClick={logout}>
              Sign out
            </Button>
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  )
}
