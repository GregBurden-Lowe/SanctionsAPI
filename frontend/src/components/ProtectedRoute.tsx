import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/context/AuthContext'

interface ProtectedRouteProps {
  children: React.ReactNode
}

/**
 * Wraps routes that require authentication. Redirects to /login if not logged in.
 */
export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { token, checked, loginRequired, user } = useAuth()
  const location = useLocation()

  if (!checked) {
    return (
      <div className="min-h-screen bg-app flex items-center justify-center">
        <p className="text-text-secondary">Loading…</p>
      </div>
    )
  }

  if (loginRequired && !token) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (token && loginRequired && user?.must_change_password) {
    return <Navigate to="/change-password" replace />
  }

  return <>{children}</>
}
