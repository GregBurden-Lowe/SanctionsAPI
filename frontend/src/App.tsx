import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from '@/layouts/AppShell'
import { ScreeningPage } from '@/pages/ScreeningPage'
import { AdminPage } from '@/pages/AdminPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<ScreeningPage />} />
          <Route path="admin" element={<AdminPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
