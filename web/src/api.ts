import type { AuthResult, AuthStatus, Mark, ReportPayload, ReportSummary, Session, TodayPayload } from './types'

/** 伺服器回了非 2xx。message 是伺服器給的 error 文字（沒有就是狀態碼）。 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
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

export const api = {
  session: () => request<Session>('/api/session'),
  today: () => request<TodayPayload>('/api/today'),
  reports: () => request<{ reports: ReportSummary[] }>('/api/reports').then((r) => r.reports),
  report: (date: string) => request<ReportPayload>(`/api/reports/${encodeURIComponent(date)}`),
  /** mark '' ＝ 取消。回傳伺服器實際記下的 mark。 */
  feedback: (uid: string, mark: Mark) => request<{ uid: string; mark: Mark }>('/api/feedback', { uid, mark }),
  auth: {
    status: () => request<AuthStatus>('/api/auth'),
    saveToken: (token: string) => request<AuthResult>('/api/auth/token', { token }),
    test: () => request<AuthResult>('/api/auth/test', {}),
    revoke: () => request<AuthResult>('/api/auth/revoke', {}),
  },
}
