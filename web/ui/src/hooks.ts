import { useEffect, useState, useSyncExternalStore } from 'react'
import { getRecoveries, getStatus, subscribe, type Status } from './connectivity'

export interface Fetched<T> {
  data?: T
  error?: Error
  loading: boolean
}

/** deps 變了就重新載入；前一次還沒回來的結果會被丟掉，不會蓋掉新的。 */
export function useFetch<T>(load: () => Promise<T>, deps: readonly unknown[]): Fetched<T> {
  const [state, setState] = useState<Fetched<T>>({ loading: true })
  useEffect(() => {
    let live = true
    setState({ loading: true })
    load().then(
      (data) => live && setState({ data, loading: false }),
      (error: unknown) => live && setState({ error: error instanceof Error ? error : new Error(String(error)), loading: false }),
    )
    return () => {
      live = false
    }
    // load 每次 render 都是新函式，是否重新載入只看呼叫端給的 deps
  }, deps)
  return state
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title
  }, [title])
}

/** 連線狀態（見 connectivity.ts）：離線、連不上伺服器、Access 登入逾時。 */
export function useConnectivity(): Status {
  return useSyncExternalStore(subscribe, getStatus, () => 'ok')
}

/** 從連不上／離線／登入逾時恢復的次數。放進 useFetch 的 deps，恢復後畫面會自己換成最新資料；一直正常時不會變。 */
export function useRecovered(): number {
  return useSyncExternalStore(subscribe, getRecoveries, () => 0)
}
