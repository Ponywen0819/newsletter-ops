import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from './api'
import type { AuthHealth } from './types'

interface AuthHealthValue {
  /** null ＝ 還沒問到（或問不到）：橫幅不顯示，也不報錯，離線、伺服器掛了另有連線橫幅 */
  health: AuthHealth | null
  /** 重新問一次；授權頁動過 token 之後呼叫 */
  refresh: () => void
}

const AuthHealthContext = createContext<AuthHealthValue>({ health: null, refresh: () => {} })

/** 最近一次驗證的結果（worker 每天驗一次）。載入時問一次，之後每次回到這個分頁／PWA 再問一次，不必輪詢。 */
export function AuthHealthProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<AuthHealth | null>(null)
  const refresh = useCallback(() => {
    api.authHealth().then(setHealth, () => {}) // 問不到就維持原樣
  }, [])
  useEffect(() => {
    refresh()
    const onVisible = () => document.visibilityState === 'visible' && refresh()
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [refresh])
  return <AuthHealthContext.Provider value={{ health, refresh }}>{children}</AuthHealthContext.Provider>
}

export function useAuthHealth(): AuthHealthValue {
  return useContext(AuthHealthContext)
}
