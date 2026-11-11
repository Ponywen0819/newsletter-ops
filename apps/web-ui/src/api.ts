import { API_CACHE } from './cacheName'
import { check } from './connectivity'
import type {
  AuthHealth,
  AuthResult,
  AuthStatus,
  FeedbackStats,
  FeedbackTarget,
  KgEntities,
  KgEntityPayload,
  KgItemPayload,
  Mark,
  ReportPayload,
  ReportSummary,
  SearchResult,
  Session,
  TodayPayload,
} from './types'

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

/** today／reports：連不上直接回快取不等逾時；登入逾時不回快取。 */
async function cachedRead<T>(path: string): Promise<T> {
  const status = await check()
  if (status === 'login') throw new AuthExpiredError()
  return status === 'ok' ? request<T>(path) : fromCache<T>(path)
}

const feedback = (uid: string, mark: Mark) => request<{ uid: string; mark: Mark }>('/api/feedback', { uid, mark })

export const api = {
  session: () => request<Session>('/api/session'),
  /** 不在 auth 底下：/api/auth* 只限本機，這個給所有人看（橫幅用） */
  authHealth: () => request<AuthHealth>('/api/auth-health'),
  today: () => cachedRead<TodayPayload>('/api/today'),
  reports: () => cachedRead<{ reports: ReportSummary[] }>('/api/reports').then((r) => r.reports),
  report: (date: string) => cachedRead<ReportPayload>(`/api/reports/${encodeURIComponent(date)}`),
  /** 不進離線副本：連不上就是失敗 */
  search: (q: string) => request<SearchResult>(`/api/search?q=${encodeURIComponent(q)}`),
  feedbackStats: () => request<FeedbackStats>('/api/feedback-stats'),
  feedbackTarget: (uid: string) => request<FeedbackTarget>(`/api/feedback/${encodeURIComponent(uid)}`),
  /** mark '' ＝ 取消。回傳伺服器實際記下的 mark。 */
  feedback,
  /** 「事實有誤」：flag '' ＝ 取消。不排隊，連不上就是失敗。 */
  kgFlag: (uid: string, flag: 'wrong' | '') => request<{ uid: string; flag: 'wrong' | '' }>('/api/kg-flag', { uid, flag }),
  /** 知識圖譜：不進離線副本，連不上就是失敗 */
  kg: {
    item: (uids: string[], depth: 1 | 2) =>
      request<KgItemPayload>(`/api/kg/item/${uids.map(encodeURIComponent).join(',')}?depth=${depth}`),
    entity: (key: string, days: number) => request<KgEntityPayload>(`/api/kg/entity/${encodeURIComponent(key)}?days=${days}`),
    entities: (q: string) => request<KgEntities>(`/api/kg/entities?q=${encodeURIComponent(q)}`),
  },
  /** 對每個 uid 各送一次。成功的不會收回，畫面要以 saved 為準。 */
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
