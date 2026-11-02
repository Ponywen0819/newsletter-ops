import { NavLink, Outlet, useLocation } from 'react-router-dom'
import type { Status } from '../connectivity'
import { useAuthHealth } from '../authHealth'
import { useConnectivity } from '../hooks'
import { useSession } from '../session'
import type { AuthHealth } from '../types'
import { ThemeSelect } from './ThemeSelect'

const BANNER: Record<Exclude<Status, 'ok'>, string> = {
  offline: '離線中，顯示的是上次載入的內容',
  unreachable: '連不上伺服器，顯示的是上次載入的內容',
  login: '登入已逾時', // 資料請求此時不回快取（見 api.ts），畫面會是「讀取失敗」；按鈕整頁重載，讓瀏覽器走 Access 登入
}

/** 認證橫幅的文字；null ＝ 不用提醒。unchecked 是伺服器剛啟動、還沒驗證過，不算問題。 */
function authMessage(h: AuthHealth): string | null {
  switch (h.state) {
    case 'missing':
      return '尚未授權 Claude，晨報無法產生'
    case 'expiring':
      return 'Claude 的 token 快到期了（依一年效期推算）'
    case 'invalid':
      return h.kind === 'api_key'
        ? 'SDK 正在使用 API key，不是 OAuth 訂閱額度'
        : 'Claude 授權已失效，下次晨報會失敗'
    case 'unknown':
      if (h.kind === 'unchecked') return null
      return h.kind === 'quota' ? '訂閱額度用完，晨報可能失敗' : '暫時無法確認 Claude 授權狀態，稍後會再檢查'
    default:
      return null
  }
}

/** 授權有問題才出現；能處理的（沒授權、快到期、失效）本機給連結（已在 /auth 就不用），遠端說明要回主機處理（/auth 只服務本機）。 */
function AuthBar({ local }: { local: boolean }) {
  const { health } = useAuthHealth()
  const onAuthPage = useLocation().pathname === '/auth'
  const message = health && authMessage(health)
  if (!health || !message) return null
  const fixable = health.state === 'missing' || health.state === 'expiring' || health.state === 'invalid'
  return (
    <div className={`auth-bar ${health.state === 'unknown' || health.state === 'expiring' ? 'auth-bar-warn' : 'auth-bar-bad'}`}>
      {message}
      {fixable && (local ? !onAuthPage && <NavLink to="/auth">前往授權</NavLink> : '，請在主機上重新授權')}
    </div>
  )
}

/**
 * reader：桌機寬度是「左側欄＋右側內文」（首頁、歷史晨報、單日晨報），頁面要自己用 ReaderShell 畫兩欄。
 * single：單欄卡片（確認頁、授權、找不到頁面）。
 * 由 App.tsx 的路由指定，不看內容渲染了什麼，所以載入中、出錯時外框也一樣。
 */
export function Layout({ variant }: { variant: 'reader' | 'single' }) {
  const session = useSession()
  const connectivity = useConnectivity()
  return (
    <div data-layout={variant}>
      <header>
        <nav>
          <span className="site-name">晨報</span>
          <NavLink to="/" end>
            今日晨報
          </NavLink>
          <NavLink to="/reports">歷史晨報</NavLink>
          {/* /auth 能寫入憑證，後端只服務本機；經 Tunnel 進來的看不到這個連結 */}
          {session?.local && <NavLink to="/auth">Claude 授權</NavLink>}
          <ThemeSelect />
        </nav>
      </header>
      {/* 常駐的 status 區：內容從無到有，輔助科技才會唸出來；沒內容時 CSS 把它藏起來 */}
      <div className="offline-bar" role="status">
        {connectivity !== 'ok' && BANNER[connectivity]}
        {connectivity === 'login' && (
          <button type="button" onClick={() => location.reload()}>
            重新登入
          </button>
        )}
      </div>
      <AuthBar local={!!session?.local} />
      <div className="page">
        <main className="card">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
