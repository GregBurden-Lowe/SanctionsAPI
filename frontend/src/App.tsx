import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/context/AuthContext'
import { AppShell } from '@/layouts/AppShell'
import { ScreeningPage, ScreeningResultPage } from '@/pages/ScreeningPage'
import { AdminPage } from '@/pages/AdminPage'
import { LoginPage } from '@/pages/LoginPage'
import { ChangePasswordPage } from '@/pages/ChangePasswordPage'
import { UsersPage } from '@/pages/UsersPage'
import { SearchDatabasePage } from '@/pages/SearchDatabasePage'
import { BulkScreeningPage } from '@/pages/BulkScreeningPage'
import { ScreeningJobsPage } from '@/pages/ScreeningJobsPage'
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

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { checked, loginRequired, token, user } = useAuth()
  if (!checked) {
    return (
      <div className="min-h-screen bg-app flex items-center justify-center">
        <p className="text-text-secondary">Loading…</p>
      </div>
    )
  }
  if (loginRequired && !token) return <Navigate to="/login" replace />
  if (loginRequired && !user?.is_admin) return <Navigate to="/" replace />
  return <>{children}</>
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
            <Route path="results" element={<ScreeningResultPage />} />
            <Route path="search" element={<SearchDatabasePage />} />
            <Route
              path="admin"
              element={
                <AdminRoute>
                  <AdminPage />
                </AdminRoute>
              }
            />
            <Route
              path="admin/users"
              element={
                <AdminRoute>
                  <UsersPage />
                </AdminRoute>
              }
            />
            <Route
              path="admin/bulk-screening"
              element={
                <AdminRoute>
                  <BulkScreeningPage />
                </AdminRoute>
              }
            />
            <Route
              path="admin/jobs"
              element={
                <AdminRoute>
                  <ScreeningJobsPage />
                </AdminRoute>
              }
            />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
