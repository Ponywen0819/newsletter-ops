import { API_CACHE } from './cacheName'
import { check } from './connectivity'
import type { AuthResult, AuthStatus, FeedbackTarget, Mark, ReportPayload, ReportSummary, Session, TodayPayload } from './types'

/** 伺服器回了非 2xx。message 是伺服器給的 error 文字（沒有就是狀態碼）。 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Access 登入逾時：不是離線，不能拿快取假裝正常。橫幅（Layout）會給「重新登入」。 */
export class AuthExpiredError extends Error {
  constructor() {
    super('登入已逾時，請重新登入')
    this.name = 'AuthExpiredError'
  }
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit =
    body === undefined
      ? {}
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
  const res = await fetch(path, init)
  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    // 回應不是 JSON：下面用狀態碼報錯
  }
  if (!res.ok) {
    const error = (data as { error?: unknown } | null)?.error
    throw new ApiError(res.status, typeof error === 'string' ? error : `HTTP ${res.status}`)
  }
  return data as T
}

/** 連不上時讀 service worker 上次存的回應（sw.ts 的 NetworkFirst 寫的）。 */
async function fromCache<T>(path: string): Promise<T> {
  const hit = typeof caches === 'undefined' ? undefined : await caches.match(path, { cacheName: API_CACHE }).catch(() => undefined)
  if (!hit) throw new Error('連不上伺服器')
  return (await hit.json()) as T
}

/**
 * 會被 service worker 快取的讀取（today／reports）：先問連線狀態。連得到照舊（網路優先，SW 更新快取）；
 * 連不上直接回快取，不等請求逾時；登入逾時不回快取。POST 與其他 GET（session、auth、feedback）不走這裡。
 */
async function cachedRead<T>(path: string): Promise<T> {
  const status = await check()
  if (status === 'login') throw new AuthExpiredError()
  return status === 'ok' ? request<T>(path) : fromCache<T>(path)
}

const feedback = (uid: string, mark: Mark) => request<{ uid: string; mark: Mark }>('/api/feedback', { uid, mark })

export const api = {
  session: () => request<Session>('/api/session'),
  today: () => cachedRead<TodayPayload>('/api/today'),
  reports: () => cachedRead<{ reports: ReportSummary[] }>('/api/reports').then((r) => r.reports),
  report: (date: string) => cachedRead<ReportPayload>(`/api/reports/${encodeURIComponent(date)}`),
  feedbackTarget: (uid: string) => request<FeedbackTarget>(`/api/feedback/${encodeURIComponent(uid)}`),
  /** mark '' ＝ 取消。回傳伺服器實際記下的 mark。 */
  feedback,
  /**
   * 一則新聞底下可能有好幾個 uid（併了多篇文章，後端每篇各產一個）：對每個各送一次。
   * saved 是寫入成功的；有任何一個失敗 failed 就是 true。成功的不會被收回，所以畫面要以 saved 為準。
   */
  feedbackAll: async (uids: string[], mark: Mark) => {
    const results = await Promise.allSettled(uids.map((uid) => feedback(uid, mark)))
    const saved = results.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []))
    return { saved, failed: saved.length < uids.length }
  },
  auth: {
    status: () => request<AuthStatus>('/api/auth'),
    saveToken: (token: string) => request<AuthResult>('/api/auth/token', { token }),
    test: () => request<AuthResult>('/api/auth/test', {}),
    revoke: () => request<AuthResult>('/api/auth/revoke', {}),
  },
}
