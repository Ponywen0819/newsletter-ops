import { useEffect, useState, useSyncExternalStore } from 'react'

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

function subscribeOnline(notify: () => void) {
  window.addEventListener('online', notify)
  window.addEventListener('offline', notify)
  return () => {
    window.removeEventListener('online', notify)
    window.removeEventListener('offline', notify)
  }
}

/**
 * 瀏覽器有沒有網路（navigator.onLine）。
 * ponytail: 只反映「有沒有網路」（飛航模式、斷線），不代表伺服器連得到；伺服器掛了時不會變成 false。
 */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true)
}
