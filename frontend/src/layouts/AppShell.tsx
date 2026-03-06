import { useEffect, useRef, useState } from 'react'
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
  BiLogOut,
} from 'react-icons/bi'
import { useNavigate } from 'react-router-dom'

/** layout.containers.AppShell + Sidebar + Main from design.json */
const appShellClass = 'min-h-screen bg-app text-text-primary'
const sidebarClass =
  'fixed left-0 top-14 bottom-0 w-[206px] bg-[#0f2340] text-white border-r border-[rgba(255,255,255,0.06)] px-4 py-4 flex flex-col overflow-y-auto'
const mainClass = 'ml-[206px] min-h-screen flex flex-col pt-14'
const topBarClass = 'fixed top-0 left-0 right-0 h-14 px-6 bg-[#0f2340] text-white border-b border-[rgba(255,255,255,0.06)] flex items-center justify-between gap-4 z-20'

export function AppShell() {
  const { user, loginRequired, logout } = useAuth()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const displayUser = (user?.username || 'greg.burden-lowe').split('@')[0]
  const initials = displayUser
    .split(/[.\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || '')
    .join('') || 'GB'

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!menuRef.current) return
      if (!menuRef.current.contains(event.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [])

  const handleSignOut = () => {
    setMenuOpen(false)
    logout()
    navigate('/login', { replace: true })
  }

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
          <div ref={menuRef} className="relative">
            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              className="flex items-center gap-2 rounded-full border border-[rgba(255,255,255,0.06)] bg-[rgba(255,255,255,0.06)] px-2.5 py-1.5"
            >
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-[#3b82f6] to-[#2563eb] text-[10px] font-bold text-white">
                {initials}
              </span>
              <span className="text-xs text-[#94a3b8]">{displayUser}</span>
              <BiChevronDown className="h-3.5 w-3.5 text-[#64748b]" aria-hidden />
            </button>
            {menuOpen && (
              <div className="absolute right-0 mt-2 min-w-[160px] rounded-lg border border-[#e2e8f0] bg-white p-1 shadow-sm">
                <button
                  type="button"
                  onClick={handleSignOut}
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-[12.5px] font-semibold text-[#475569] hover:bg-[#f1f5f9]"
                >
                  <BiLogOut className="h-4 w-4" />
                  Sign out
                </button>
              </div>
            )}
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  )
}
