import { NavLink, Outlet, useLocation } from 'react-router-dom'
import type { Status } from '../connectivity'
import { useAuthHealth } from '../authHealth'
import { useConnectivity } from '../hooks'
import { useSession } from '../session'
import { lastSync, type SyncRecord } from '../sync'
import type { AuthHealth } from '../types'
import { ThemeSelect } from './ThemeSelect'

const BANNER: Record<Exclude<Status, 'ok'>, string> = {
  offline: '離線中',
  unreachable: '連不上伺服器',
  login: '登入已逾時', // 按鈕整頁重載，讓瀏覽器走 Access 登入
}

const pad = (n: number) => String(n).padStart(2, '0')

/** 離線／連不上時，說明顯示的是哪一次同步的副本；從沒同步過就退回籠統的說法。 */
function copyNote(rec: SyncRecord | null): string {
  if (!rec) return '，顯示的是上次載入的內容'
  const d = new Date(rec.at)
  const when = `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  const missed = rec.failed ? `，${rec.failed} 份沒更新到` : ''
  return `，顯示的是 ${when} 同步的副本（共 ${rec.count} 份${missed}）`
}

/** 認證橫幅的文字；null ＝ 不用提醒。 */
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

/** 授權有問題才出現；本機給 /auth 連結，遠端只說要回主機處理。 */
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

/** reader：兩欄（頁面自己用 ReaderShell）；single：單欄卡片。由路由指定，載入中與出錯時外框不變。 */
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
        {(connectivity === 'offline' || connectivity === 'unreachable') && copyNote(lastSync())}
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
