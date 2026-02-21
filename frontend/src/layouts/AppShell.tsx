import { Outlet } from 'react-router-dom'
import { NavItem, Button } from '@/components'
import { useAuth } from '@/context/AuthContext'

/** layout.containers.AppShell + Sidebar + Main from design.json */
const appShellClass = 'min-h-screen bg-app text-text-primary'
const sidebarClass =
  'fixed inset-y-0 left-0 w-72 bg-[#0f1f2b] text-white border-r border-white/10 px-6 py-6 flex flex-col overflow-y-auto shadow-lg'
const mainClass = 'ml-72 min-h-screen flex flex-col'
const topBarClass = 'h-20 px-10 flex items-center justify-between gap-6'

function HomeIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
    </svg>
  )
}

function SettingsIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  )
}

function SearchIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
    </svg>
  )
}

function UsersIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
    </svg>
  )
}

function UploadIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M12 16V4m0 0l-4 4m4-4l4 4" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
    </svg>
  )
}

function QueueIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M8 6h13M8 12h13M8 18h13" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M3 6h.01M3 12h.01M3 18h.01" />
    </svg>
  )
}

function KeyIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M15 7a5 5 0 11-9.9 1H2v2h2v2h2v2h3l1.3-1.3A5 5 0 0115 7z" />
    </svg>
  )
}

export function AppShell() {
  const { user, logout, loginRequired } = useAuth()
  return (
    <div className={appShellClass}>
      <aside className={sidebarClass}>
        <nav className="flex flex-col gap-1 mt-1" aria-label="Primary">
          <NavItem to="/" icon={<HomeIcon />}>
            Screening
          </NavItem>
          <NavItem to="/search" icon={<SearchIcon />}>
            Search database
          </NavItem>
          {(!loginRequired || user?.is_admin) && (
            <NavItem to="/admin" icon={<SettingsIcon />}>
              Admin
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/users" icon={<UsersIcon />}>
              Users
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/bulk-screening" icon={<UploadIcon />}>
              Bulk screening
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/jobs" icon={<QueueIcon />}>
              Screening jobs
            </NavItem>
          )}
          {user?.is_admin && (
            <NavItem to="/admin/api-keys" icon={<KeyIcon />}>
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
