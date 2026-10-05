import { NavLink, Outlet } from 'react-router-dom'
import { useSession } from '../session'
import { ThemeSelect } from './ThemeSelect'

export function Layout() {
  const session = useSession()
  return (
    <>
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
      <div className="page">
        <main className="card">
          <Outlet />
        </main>
      </div>
    </>
  )
}
