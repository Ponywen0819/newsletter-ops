/**
 * 連得到伺服器嗎？探測 GET /api/heartbeat，結果分四種：
 *   ok          連得到
 *   offline     navigator.onLine 是 false（不探測，直接判定）
 *   unreachable 網路錯誤、逾時、或任何非 2xx（Tunnel 在、origin 掛時 Cloudflare 回的是 502／530，不是網路錯誤）
 *   login       Cloudflare Access 登入逾時：heartbeat 被 302 到別的來源（redirect: 'manual' 會得到 opaqueredirect），
 *               或被 Access 擋成 401／403。這跟離線不同：不能拿快取假裝一切正常。
 *
 * 探測時機：第一個訂閱者出現時、online／offline 事件、回到前景（這三種都強制重探），以及資料請求前（check()，吃重用期）。
 * 不做常駐輪詢。沒有 React 依賴；hooks.ts 用 subscribe／getStatus 接 useSyncExternalStore。
 */
export type Status = 'ok' | 'offline' | 'unreachable' | 'login'

const TIMEOUT_MS = 2000
const REUSE_MS = 5000 // 重用期內不重打；成功與失敗都重用，免得連續換頁每次都等逾時

let status: Status = 'ok' // 樂觀：還沒探測前橫幅是空的
let recoveries = 0 // 從非 ok 回到 ok 的次數；頁面把它放進 useFetch 的 deps，恢復後自動重抓
let checkedAt = -Infinity
let inflight: Promise<Status> | null = null
const listeners = new Set<() => void>()

export const getStatus = () => status
export const getRecoveries = () => recoveries

function set(next: Status) {
  if (next === status) return
  if (status !== 'ok' && next === 'ok') recoveries++
  status = next
  listeners.forEach((l) => l())
}

async function probe(): Promise<Status> {
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), TIMEOUT_MS) // 不用 AbortSignal.timeout：測試的 fake timers 推不動它
  try {
    const res = await fetch('/api/heartbeat', { cache: 'no-store', redirect: 'manual', signal: abort.signal })
    void res.body?.cancel() // 只看狀態，不讀內容：放掉連線（opaqueredirect 沒有 body）
    if (res.type === 'opaqueredirect' || res.status === 401 || res.status === 403) return 'login'
    return res.ok ? 'ok' : 'unreachable'
  } catch {
    return 'unreachable'
  } finally {
    clearTimeout(timer)
  }
}

/** 目前的連線狀態。force 才無視重用期；同時多個呼叫共用同一個進行中的探測。 */
export function check(force = false): Promise<Status> {
  if (!navigator.onLine) {
    set('offline')
    return Promise.resolve(status)
  }
  if (inflight) return inflight
  if (!force && Date.now() - checkedAt < REUSE_MS) return Promise.resolve(status)
  inflight = probe()
    .then((next) => {
      checkedAt = Date.now()
      set(next)
      return next
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}

const recheck = () => void check(true)
const onVisibility = () => document.visibilityState === 'visible' && recheck()

export function subscribe(notify: () => void): () => void {
  listeners.add(notify)
  if (listeners.size === 1) {
    window.addEventListener('online', recheck)
    window.addEventListener('offline', recheck)
    document.addEventListener('visibilitychange', onVisibility)
    void check()
  }
  return () => {
    listeners.delete(notify)
    if (listeners.size === 0) {
      window.removeEventListener('online', recheck)
      window.removeEventListener('offline', recheck)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }
}

/** 給測試用：模組狀態是全域的，每個測試之間要清掉。 */
export function resetConnectivity(): void {
  status = 'ok'
  recoveries = 0
  checkedAt = -Infinity
  inflight = null
}
