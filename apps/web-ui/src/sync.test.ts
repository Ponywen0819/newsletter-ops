import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { KEEP, lastSync, resetSync, sync } from './sync'
import { mockFetch } from './test/fetchMock'

/** 假的 Cache Storage：一個 cache，path → 內容。 */
function stubCaches(initial: Record<string, unknown> = {}) {
  const store = new Map(Object.entries(initial).map(([p, v]) => [p, JSON.stringify(v)]))
  const cache = {
    put: vi.fn(async (path: string, res: Response) => void store.set(path, await res.text())),
    keys: async () => [...store.keys()].map((p) => new Request(`http://localhost${p}`)),
    delete: async (req: Request) => store.delete(new URL(req.url).pathname),
  }
  const open = vi.fn(async () => cache)
  vi.stubGlobal('caches', { open })
  return { store, open, cache }
}

const dates = (n: number) => Array.from({ length: n }, (_, i) => `2026-09-${String(30 - i).padStart(2, '0')}`)
const routesFor = (all: string[]) => ({
  'GET /api/reports': () => ({ json: { reports: all.map((date) => ({ date, headline: date })) } }),
  'GET /api/today': () => ({ json: { date: all[0] } }),
  ...Object.fromEntries(all.map((d) => [`GET /api/reports/${d}`, () => ({ json: { date: d } })])),
})

beforeEach(() => {
  resetSync()
  localStorage.removeItem('offline-sync')
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('sync（離線副本）', () => {
  it('存今日、列表與最近的晨報，清掉不在最近 KEEP 份裡的', async () => {
    const all = dates(KEEP + 2)
    const { store } = stubCaches({ '/api/reports/2020-01-01': { old: true } })
    mockFetch(routesFor(all))
    expect(await sync(true)).toBe(true)
    const kept = [...store.keys()].filter((p) => p.startsWith('/api/reports/')).toSorted()
    expect(kept).toEqual(all.slice(0, KEEP).map((d) => `/api/reports/${d}`).toSorted())
    expect(store.has('/api/today') && store.has('/api/reports')).toBe(true)
  })

  it('請求都是 no-store、不跟轉址：Access 逾時的登入頁不能存進副本', async () => {
    stubCaches()
    const net = mockFetch(routesFor(dates(1)))
    await sync(true)
    const inits = net.fn.mock.calls.filter(([p]) => String(p) !== '/api/heartbeat').map(([, init]) => init)
    expect(inits.every((i) => i?.cache === 'no-store' && i?.redirect === 'manual')).toBe(true)
  })

  it('單份失敗：其餘照存，舊的那份留著', async () => {
    const all = dates(3)
    const { store } = stubCaches({ [`/api/reports/${all[1]}`]: { date: 'old' } })
    mockFetch({ ...routesFor(all), [`GET /api/reports/${all[1]}`]: () => ({ status: 500, json: {} }) })
    expect(await sync(true)).toBe(true)
    expect(store.get(`/api/reports/${all[1]}`)).toBe(JSON.stringify({ date: 'old' }))
    expect(store.has(`/api/reports/${all[0]}`)).toBe(true)
  })

  it('列表失敗：什麼都不動', async () => {
    const { store, cache } = stubCaches({ '/api/reports/2026-01-01': { x: 1 } })
    mockFetch({ 'GET /api/reports': () => ({ status: 502, json: {} }) })
    expect(await sync(true)).toBe(false)
    expect(cache.put).not.toHaveBeenCalled()
    expect([...store.keys()]).toEqual(['/api/reports/2026-01-01'])
  })

  it.each([
    ['連不上', { status: 502, json: {} }],
    ['登入逾時', { status: 403, json: {} }],
  ])('%s：不發資料請求', async (_name, heartbeat) => {
    const { open } = stubCaches()
    const net = mockFetch({ 'GET /api/heartbeat': () => heartbeat })
    expect(await sync(true)).toBe(false)
    expect(net.calls.map((c) => c.path)).toEqual(['/api/heartbeat'])
    expect(open).not.toHaveBeenCalled()
  })

  it('記下同步時間與份數；沒更新到的另外算', async () => {
    const all = dates(3)
    stubCaches({ [`/api/reports/${all[1]}`]: { date: 'old' } })
    mockFetch({ ...routesFor(all), [`GET /api/reports/${all[1]}`]: () => ({ status: 500, json: {} }), [`GET /api/reports/${all[2]}`]: () => ({ status: 500, json: {} }) })
    const before = Date.now()
    await sync(true)
    const rec = lastSync()
    expect(rec && rec.at >= before).toBe(true)
    expect(rec).toMatchObject({ count: 2, failed: 2 }) // 一份新存的＋一份留著舊版；兩份沒更新到
  })

  it('一份都沒寫入、或列表失敗：不算同步過', async () => {
    stubCaches()
    mockFetch({ ...routesFor(dates(1)), [`GET /api/reports/${dates(1)[0]}`]: () => ({ status: 500, json: {} }) })
    await sync(true)
    expect(lastSync()).toBeNull()
    mockFetch({ 'GET /api/reports': () => ({ status: 502, json: {} }) })
    await sync(true)
    expect(lastSync()).toBeNull()
  })

  it('沒有晨報也算同步過（共 0 份）', async () => {
    stubCaches()
    mockFetch(routesFor([]))
    await sync(true)
    expect(lastSync()).toMatchObject({ count: 0, failed: 0 })
  })

  it('localStorage 不能用：同步照常，紀錄讀不到', async () => {
    stubCaches()
    mockFetch(routesFor(dates(1)))
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(await sync(true)).toBe(true)
    expect(lastSync()).toBeNull()
  })

  it('沒有 Cache API：什麼都不做', async () => {
    vi.stubGlobal('caches', undefined)
    const net = mockFetch({})
    expect(await sync(true)).toBe(false)
    expect(net.calls).toEqual([])
  })

  it('剛同步過：不帶 force 不再同步；同時呼叫只跑一次', async () => {
    stubCaches()
    const net = mockFetch(routesFor(dates(1)))
    const [a, b] = await Promise.all([sync(true), sync(true)])
    expect(a && b).toBe(true)
    const n = net.calls.length
    expect(await sync()).toBe(false)
    expect(net.calls.length).toBe(n)
    expect(await sync(true)).toBe(true)
  })
})
