/**
 * 探測 GET /api/heartbeat：ok／offline／unreachable（含 Cloudflare 的 502、530）／login（Access 逾時：
 * opaqueredirect 或 401、403，不能拿快取假裝正常）。不做常駐輪詢。
 */
export type Status = 'ok' | 'offline' | 'unreachable' | 'login'

const TIMEOUT_MS = 2000
const REUSE_MS = 5000

let status: Status = 'ok'
let recoveries = 0 // 從非 ok 回到 ok 的次數
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
    void res.body?.cancel()
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
