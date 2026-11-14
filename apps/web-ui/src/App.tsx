import { lazy, type ReactNode, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import { AuthHealthProvider } from './authHealth'
import { Layout } from './components/Layout'
import { Loading } from './components/Notice'
import { useOfflineSync } from './hooks'
import { SessionProvider } from './session'
import { AuthPage } from './pages/AuthPage'
import { FeedbackPage } from './pages/FeedbackPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { ReportListPage } from './pages/ReportListPage'
import { ReportPage } from './pages/ReportPage'
import { StatsPage } from './pages/StatsPage'
import { TodayPage } from './pages/TodayPage'

// 知識圖譜整組另外打包，用到才載入：晨報本身（也是離線副本）不必多帶畫圖的程式
const kgPage = (name: 'KgIndexPage' | 'KgItemPage' | 'KgEntityPage') =>
  lazy(() => import('./pages/KgPages').then((m) => ({ default: m[name] })))
const KgIndexPage = kgPage('KgIndexPage')
const KgItemPage = kgPage('KgItemPage')
const KgEntityPage = kgPage('KgEntityPage')
// 維運頁只有本機會用到，也另外打包
const opsPage = (name: 'OpsPage' | 'OpsMetricsPage' | 'OpsConfigPage') =>
  lazy(() => import('./pages/OpsPages').then((m) => ({ default: m[name] })))
const OpsPage = opsPage('OpsPage')
const OpsMetricsPage = opsPage('OpsMetricsPage')
const OpsConfigPage = opsPage('OpsConfigPage')
const later = (page: ReactNode) => <Suspense fallback={<Loading />}>{page}</Suspense>

// 這幾條路由要和 apps/web-server 的 SPA_ROUTES 一致：後端對它們回 index.html，其他路徑回 404 的 index.html。
// 版面分兩組由路由決定（見 Layout）；新增路由時想一下它屬於哪一組。
export function App() {
  useOfflineSync()
  return (
    <SessionProvider>
      <AuthHealthProvider>
        <Routes>
          <Route element={<Layout variant="reader" />}>
            <Route index element={<TodayPage />} />
            <Route path="reports" element={<ReportListPage />} />
            <Route path="reports/:date" element={<ReportPage />} />
          </Route>
          <Route element={<Layout variant="single" />}>
            <Route path="feedback/:uid" element={<FeedbackPage />} />
            <Route path="stats" element={<StatsPage />} />
            <Route path="kg" element={later(<KgIndexPage />)} />
            <Route path="kg/item/:uids" element={later(<KgItemPage />)} />
            <Route path="kg/entity/:key" element={later(<KgEntityPage />)} />
            <Route path="auth" element={<AuthPage />} />
            <Route path="ops" element={later(<OpsPage />)} />
            <Route path="ops/metrics" element={later(<OpsMetricsPage />)} />
            <Route path="ops/config" element={later(<OpsConfigPage />)} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </AuthHealthProvider>
    </SessionProvider>
  )
}
