import { type AnchorHTMLAttributes } from 'react'
import { Link, useLocation } from 'react-router-dom'

const base =
  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-white/75 hover:bg-white/10 hover:text-white transition'
const activeClass = 'bg-white/14 text-white'

export interface NavItemProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string
  icon?: React.ReactNode
  children: React.ReactNode
}

export function NavItem({ to, icon, children, className = '', ...props }: NavItemProps) {
  const location = useLocation()
  const isActive = location.pathname === to || (to !== '/' && location.pathname.startsWith(to))
  return (
    <Link
      to={to}
      className={`${base} ${isActive ? activeClass : ''} ${className}`.trim()}
      {...props}
    >
      {icon && <span className="h-5 w-5 text-current flex-shrink-0" aria-hidden>{icon}</span>}
      {children}
    </Link>
  )
}
