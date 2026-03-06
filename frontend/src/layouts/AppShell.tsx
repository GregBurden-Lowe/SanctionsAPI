import { Outlet } from 'react-router-dom'
import { NavItem } from '@/components'
import { useAuth } from '@/context/AuthContext'
import {
  BiBuildingHouse,
  BiChevronDown,
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
const topBarClass = 'fixed top-0 left-0 right-0 h-14 px-6 bg-[#0f2340] text-white border-b border-[rgba(255,255,255,0.06)] flex items-center justify-between gap-4 z-20'

export function AppShell() {
  const { user, loginRequired } = useAuth()
  const displayUser = (user?.username || 'greg.burden-lowe').split('@')[0]
  const initials = displayUser
    .split(/[.\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || '')
    .join('') || 'GB'
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
            <h1 className="text-[14.5px] font-bold text-white">Sanctions & PEP Screening</h1>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-[rgba(255,255,255,0.06)] bg-[rgba(255,255,255,0.06)] px-2.5 py-1.5">
            <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-[#3b82f6] to-[#2563eb] text-[10px] font-bold text-white">
              {initials}
            </span>
            <span className="text-xs text-[#94a3b8]">{displayUser}</span>
            <BiChevronDown className="h-3.5 w-3.5 text-[#64748b]" aria-hidden />
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  )
}
