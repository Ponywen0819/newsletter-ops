import { useEffect, useState, useSyncExternalStore } from 'react'
import { getRecoveries, getStatus, subscribe, type Status } from './connectivity'
import { flush } from './feedbackQueue'
import { adoptBackgroundRecord, sync } from './sync'

export interface Fetched<T> {
  data?: T
  error?: Error
  loading: boolean
}

/** deps 變了就重新載入；前一次還沒回來的結果會被丟掉，不會蓋掉新的。 */
export function useFetch<T>(load: () => Promise<T>, deps: readonly unknown[]): Fetched<T> {
  const [state, setState] = useState<Fetched<T>>({ loading: true })
  /* oxlint-disable react-hooks/exhaustive-deps -- deps 由呼叫端給 */
  useEffect(() => {
    let live = true
    // oxlint-disable-next-line react/set-state-in-effect -- 換 deps 時要先回到載入中
    setState({ loading: true })
    load().then(
      (data) => live && setState({ data, loading: false }),
      (error: unknown) => live && setState({ error: error instanceof Error ? error : new Error(String(error)), loading: false }),
    )
    return () => {
      live = false
    }
  }, deps)
  /* oxlint-enable react-hooks/exhaustive-deps */
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

/** 離線副本的同步時機（sync.ts）：啟動、恢復連線時一定同步；回到前景時隔一段時間才同步。 */
export function useOfflineSync(): void {
  const recovered = useRecovered()
  useEffect(() => {
    void adoptBackgroundRecord() // 背景同步過的紀錄先搬進來，離線橫幅才說得出副本的新舊
  }, [])
  useEffect(() => {
    void flush().finally(() => void sync(true)) // 先補送離線時的標記，副本才會拿到最新的 marks
  }, [recovered])
  useEffect(() => {
    const onVisible = () => document.visibilityState === 'visible' && void sync()
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])
}
