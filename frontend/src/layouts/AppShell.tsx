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
  'fixed left-0 top-14 bottom-0 w-[206px] bg-[#0f2340] text-white border-r border-[rgba(255,255,255,0.06)] px-4 py-4 flex flex-col overflow-y-auto'
const mainClass = 'ml-[206px] min-h-screen flex flex-col pt-14'
const topBarClass = 'fixed top-0 left-0 right-0 h-14 pl-[222px] pr-6 bg-[#0f2340] text-white border-b border-[rgba(255,255,255,0.06)] flex items-center justify-between gap-4 z-20'

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
            <h1 className="text-lg font-semibold text-white">Sanctions & PEP Screening</h1>
          </div>
          <div className="flex items-center gap-4">
            <span className="rounded-lg border border-[rgba(255,255,255,0.12)] bg-white/10 px-3 py-1.5 text-xs text-white/90">
              {user?.username}
            </span>
            <Button variant="secondary" size="sm" onClick={logout}>
              Sign out
            </Button>
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  )
}
