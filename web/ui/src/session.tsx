import { createContext, useContext, type ReactNode } from 'react'
import { api } from './api'
import { useFetch } from './hooks'
import type { Session } from './types'

// null ＝ 還在問伺服器。問不到（伺服器掛了）就當作不是本機：只會少一個導覽連結，不會多給權限。
const SessionContext = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const { data, error } = useFetch(api.session, [])
  const session = data ?? (error ? { local: false } : null)
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>
}

export function useSession(): Session | null {
  return useContext(SessionContext)
}
