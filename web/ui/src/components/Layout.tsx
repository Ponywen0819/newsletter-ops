import { NavLink, Outlet } from 'react-router-dom'
import { useSession } from '../session'

/**
 * reader：桌機寬度是「左側欄＋右側內文」（首頁、歷史晨報、單日晨報），頁面要自己用 ReaderShell 畫兩欄。
 * single：單欄卡片（確認頁、授權、找不到頁面）。
 * 由 App.tsx 的路由指定，不看內容渲染了什麼，所以載入中、出錯時外框也一樣。
 */
export function Layout({ variant }: { variant: 'reader' | 'single' }) {
  const session = useSession()
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
        </nav>
      </header>
      <div className="page">
        <main className="card">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
