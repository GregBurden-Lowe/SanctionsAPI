import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/context/AuthContext'
import { AppShell } from '@/layouts/AppShell'
import { ScreeningPage } from '@/pages/ScreeningPage'
import { AdminPage } from '@/pages/AdminPage'
import { LoginPage } from '@/pages/LoginPage'
import { ChangePasswordPage } from '@/pages/ChangePasswordPage'
import { UsersPage } from '@/pages/UsersPage'
import { ProtectedRoute } from '@/components'

function ChangePasswordGate() {
  const { token, checked, user } = useAuth()
  if (!checked) {
    return (
      <div className="min-h-screen bg-app flex items-center justify-center">
        <p className="text-text-secondary">Loading…</p>
      </div>
    )
  }
  if (!token) return <Navigate to="/login" replace />
  if (!user?.must_change_password) return <Navigate to="/" replace />
  return <ChangePasswordPage />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/change-password" element={<ChangePasswordGate />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <AppShell />
              </ProtectedRoute>
            }
          >
            <Route index element={<ScreeningPage />} />
            <Route path="admin" element={<AdminPage />} />
            <Route path="admin/users" element={<UsersPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
