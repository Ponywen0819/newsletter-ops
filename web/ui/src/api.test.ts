import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, AuthExpiredError } from './api'
import { mockFetch } from './test/fetchMock'

const today = { date: '2026-09-28', latest: '2026-09-28', report: null, marks: {} }
const READS = [
  ['today', '/api/today', () => api.today()],
  ['reports', '/api/reports', () => api.reports()],
  ['report', '/api/reports/2026-09-28', () => api.report('2026-09-28')],
] as const

/** 假的 Cache API：hit 是 path → 內容；沒列到的算沒存過。 */
function stubCaches(hit: Record<string, unknown>) {
  const match = vi.fn(async (path: string) => (path in hit ? new Response(JSON.stringify(hit[path])) : undefined))
  vi.stubGlobal('caches', { match })
  return match
}
const paths = (net: ReturnType<typeof mockFetch>) => net.calls.map((c) => c.path)

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe.each(READS)('api.%s（快取讀取）', (_name, path, read) => {
  it('連得到：照舊走網路，不碰快取', async () => {
    const match = stubCaches({})
    const net = mockFetch({ [`GET ${path}`]: () => ({ json: path === '/api/reports' ? { reports: [] } : today }) })
    await read()
    expect(paths(net)).toEqual(['/api/heartbeat', path])
    expect(match).not.toHaveBeenCalled()
  })

  it('連不上伺服器：直接回快取，不發資料請求', async () => {
    const match = stubCaches({ [path]: path === '/api/reports' ? { reports: [] } : today })
    const net = mockFetch({ 'GET /api/heartbeat': () => ({ status: 502, json: {} }) })
    await read()
    expect(paths(net)).toEqual(['/api/heartbeat'])
    expect(match).toHaveBeenCalledWith(path, { cacheName: 'api' })
  })

  it('沒有網路：不探測、不發請求，只讀快取', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    stubCaches({ [path]: path === '/api/reports' ? { reports: [] } : today })
    const net = mockFetch({})
    await read()
    expect(net.calls).toEqual([])
  })

  it('連不上又沒存過：丟「連不上伺服器」（頁面顯示讀取失敗）', async () => {
    stubCaches({})
    mockFetch({ 'GET /api/heartbeat': () => ({ status: 502, json: {} }) })
    await expect(read()).rejects.toThrow('連不上伺服器')
  })

  it('登入逾時：不回快取，丟 AuthExpiredError', async () => {
    const match = stubCaches({ [path]: today })
    const net = mockFetch({ 'GET /api/heartbeat': () => ({ status: 403, json: {} }) })
    await expect(read()).rejects.toBeInstanceOf(AuthExpiredError)
    expect(match).not.toHaveBeenCalled()
    expect(paths(net)).toEqual(['/api/heartbeat'])
  })
})

describe('快取讀取：細節', () => {
  it('沒有 Cache API（非安全環境）：不會炸，當作沒存過', async () => {
    vi.stubGlobal('caches', undefined)
    mockFetch({ 'GET /api/heartbeat': () => ({ status: 502, json: {} }) })
    await expect(api.today()).rejects.toThrow('連不上伺服器')
  })

  it('重用期內連續讀三種資料，只多一次 heartbeat', async () => {
    const net = mockFetch({
      'GET /api/today': () => ({ json: today }),
      'GET /api/reports': () => ({ json: { reports: [] } }),
      'GET /api/reports/2026-09-28': () => ({ json: today }),
    })
    await api.today()
    await api.reports()
    await api.report('2026-09-28')
    expect(paths(net).filter((p) => p === '/api/heartbeat')).toHaveLength(1)
    expect(net.calls).toHaveLength(4)
  })
})

describe('不經過閘門的請求：行為不變', () => {
  it('session、auth、feedback（GET 與 POST）不探測、不讀快取，連不上時照樣報錯', async () => {
    const match = stubCaches({})
    const net = mockFetch({
      'GET /api/heartbeat': () => ({ status: 502, json: {} }),
      'GET /api/session': () => ({ json: { local: false } }),
      'GET /api/auth': () => ({ json: { state: 'missing', env_token: false } }),
      'GET /api/feedback/abc': () => ({ json: { uid: 'abc' } }),
      'POST /api/feedback': () => ({ json: { uid: 'abc', mark: '+' } }),
    })
    await api.session()
    await api.auth.status()
    await api.feedbackTarget('abc')
    await api.feedback('abc', '+')
    await expect(api.auth.test()).rejects.toThrow() // 沒有路由 → 404，不是 AuthExpiredError 也不是快取
    expect(paths(net)).not.toContain('/api/heartbeat')
    expect(match).not.toHaveBeenCalled()
  })
})
