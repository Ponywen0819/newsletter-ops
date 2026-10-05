import { Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { SessionProvider } from './session'
import { AuthPage } from './pages/AuthPage'
import { FeedbackPage } from './pages/FeedbackPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { ReportListPage } from './pages/ReportListPage'
import { ReportPage } from './pages/ReportPage'
import { TodayPage } from './pages/TodayPage'

// 這幾條路由要和 web/server 的 SPA_ROUTES 一致：後端對它們回 index.html，其他路徑回 404 的 index.html。
// 版面分兩組由路由決定（見 Layout）；新增路由時想一下它屬於哪一組。
export function App() {
  return (
    <SessionProvider>
      <Routes>
        <Route element={<Layout variant="reader" />}>
          <Route index element={<TodayPage />} />
          <Route path="reports" element={<ReportListPage />} />
          <Route path="reports/:date" element={<ReportPage />} />
        </Route>
        <Route element={<Layout variant="single" />}>
          <Route path="feedback/:uid" element={<FeedbackPage />} />
          <Route path="auth" element={<AuthPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </SessionProvider>
  )
}
