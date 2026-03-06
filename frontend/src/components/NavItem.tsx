import { type AnchorHTMLAttributes } from 'react'
import { Link, useLocation } from 'react-router-dom'

const base =
  'flex items-center gap-3 rounded-r-lg border-l-[3px] border-transparent px-3 py-2 text-sm font-medium text-white/70 hover:bg-white/8 hover:text-white transition'
const activeClass = 'border-l-[#3b82f6] bg-[rgba(59,130,246,0.14)] text-[#93c5fd]'

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
