import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { refresh, RECORD_URL } from './offlineCopy'
import { adoptBackgroundRecord, askPersist, KEEP, lastSync, registerPeriodicSync, resetSync, sync } from './sync'
import { mockFetch } from './test/fetchMock'

/** 假的 Cache Storage：一個 cache，path → 內容。 */
function stubCaches(initial: Record<string, unknown> = {}) {
  const store = new Map(Object.entries(initial).map(([p, v]) => [p, JSON.stringify(v)]))
  const etags = new Map<string, string>()
  const cache = {
    put: vi.fn(async (path: string, res: Response) => {
      store.set(path, await res.text())
      const etag = res.headers.get('ETag')
      if (etag) etags.set(path, etag)
    }),
    match: async (path: string) =>
      store.has(path) ? new Response(store.get(path), { headers: etags.has(path) ? { ETag: etags.get(path) as string } : {} }) : undefined,
    keys: async () => [...store.keys()].map((p) => new Request(`http://localhost${p}`)),
    delete: async (req: Request) => store.delete(new URL(req.url).pathname),
  }
  const open = vi.fn(async () => cache)
  vi.stubGlobal('caches', { open })
  return { store, open, cache, etags }
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
  localStorage.removeItem('storage-persist-asked')
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

  it('條件請求：帶副本的 ETag，304 就沿用舊副本、不覆寫', async () => {
    const all = dates(2)
    const { store, etags, cache } = stubCaches({ [`/api/reports/${all[0]}`]: { date: 'cached' } })
    etags.set(`/api/reports/${all[0]}`, '"v1"')
    const net = mockFetch({
      ...routesFor(all),
      [`GET /api/reports/${all[0]}`]: () => ({ status: 304, json: null }),
    })
    expect(await sync(true)).toBe(true)
    const sent = net.calls.find((c) => c.path === `/api/reports/${all[0]}`)
    expect(sent?.headers).toEqual({ 'If-None-Match': '"v1"' })
    expect(net.calls.find((c) => c.path === `/api/reports/${all[1]}`)?.headers).toEqual({}) // 沒存過就不帶
    expect(store.get(`/api/reports/${all[0]}`)).toBe(JSON.stringify({ date: 'cached' }))
    expect(cache.put.mock.calls.map(([p]) => p)).not.toContain(`/api/reports/${all[0]}`)
    expect(lastSync()).toMatchObject({ count: 2, failed: 0 }) // 304 算更新到了
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

describe('askPersist（storage.persist）', () => {
  function stubStorage(persisted: boolean, granted = true) {
    const storage = { persisted: vi.fn(async () => persisted), persist: vi.fn(async () => granted) }
    setStorage(storage)
    return storage
  }
  // jsdom 沒有 navigator.storage，直接掛上去、測完拿掉
  const setStorage = (value: unknown) => Object.defineProperty(navigator, 'storage', { value, configurable: true })
  afterEach(() => void Reflect.deleteProperty(navigator, 'storage'))

  it('第一次同步成功後問一次；之後不再問', async () => {
    const storage = stubStorage(false)
    stubCaches()
    mockFetch(routesFor(dates(1)))
    await sync(true)
    await vi.waitFor(() => expect(storage.persist).toHaveBeenCalledTimes(1))
    await askPersist()
    expect(storage.persist).toHaveBeenCalledTimes(1)
  })

  it('已經是持久的：不問', async () => {
    const storage = stubStorage(true)
    await askPersist()
    expect(storage.persist).not.toHaveBeenCalled()
  })

  it('被拒絕：不報錯，也不再問', async () => {
    const storage = stubStorage(false, false)
    await askPersist()
    await askPersist()
    expect(storage.persist).toHaveBeenCalledTimes(1)
  })

  it('不支援：什麼都不做', async () => {
    setStorage(undefined)
    await expect(askPersist()).resolves.toBeUndefined()
  })

  it('同步失敗：不問', async () => {
    const storage = stubStorage(false)
    stubCaches()
    mockFetch({ 'GET /api/reports': () => ({ status: 502, json: {} }) })
    await sync(true)
    expect(storage.persisted).not.toHaveBeenCalled()
  })
})

describe('背景同步（service worker 的 periodicsync 用的 refresh）', () => {
  it('紀錄也寫進 cache：service worker 沒有 localStorage', async () => {
    const { store } = stubCaches()
    mockFetch(routesFor(dates(2)))
    const rec = await refresh()
    expect(rec).toMatchObject({ count: 2, failed: 0 })
    expect(JSON.parse(store.get(RECORD_URL) as string)).toEqual(rec)
  })

  it.each([
    ['登入逾時（列表被轉址）', () => mockFetch({ 'GET /api/reports': () => ({ status: 302, json: {} }) })],
    ['離線（fetch 丟錯）', () => vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))))],
  ])('%s：不寫入任何東西', async (_name, setup) => {
    const { cache } = stubCaches()
    setup()
    expect(await refresh()).toBe(false)
    expect(cache.put).not.toHaveBeenCalled()
  })

  it('頁面啟動時把背景同步的紀錄搬進 localStorage（只搬比較新的）', async () => {
    const newer = { at: 2000, count: 3, failed: 0 }
    stubCaches({ [RECORD_URL]: newer })
    localStorage.setItem('offline-sync', JSON.stringify({ at: 1000, count: 1, failed: 0 }))
    await adoptBackgroundRecord()
    expect(lastSync()).toEqual(newer)
    stubCaches({ [RECORD_URL]: { at: 500, count: 9, failed: 0 } })
    await adoptBackgroundRecord()
    expect(lastSync()).toEqual(newer)
  })
})

describe('registerPeriodicSync', () => {
  function stubSW(periodicSync: unknown, state?: PermissionState) {
    Object.defineProperty(navigator, 'serviceWorker', { value: { ready: Promise.resolve({ periodicSync }) }, configurable: true })
    Object.defineProperty(navigator, 'permissions', {
      value: state ? { query: vi.fn(async () => ({ state })) } : undefined,
      configurable: true,
    })
  }
  afterEach(() => {
    Reflect.deleteProperty(navigator, 'serviceWorker')
    Reflect.deleteProperty(navigator, 'permissions')
  })

  it('支援而且有權限：註冊，間隔 12 小時', async () => {
    const register = vi.fn(async () => {})
    stubSW({ register }, 'granted')
    await registerPeriodicSync()
    expect(register).toHaveBeenCalledWith('offline-copy', { minInterval: 12 * 60 * 60_000 })
  })

  it('沒權限：不註冊', async () => {
    const register = vi.fn(async () => {})
    stubSW({ register }, 'denied')
    await registerPeriodicSync()
    expect(register).not.toHaveBeenCalled()
  })

  it('不支援、或註冊失敗：不報錯', async () => {
    stubSW(undefined, 'granted')
    await expect(registerPeriodicSync()).resolves.toBeUndefined()
    stubSW({ register: async () => Promise.reject(new Error('NotAllowedError')) }, 'granted')
    await expect(registerPeriodicSync()).resolves.toBeUndefined()
  })
})
