import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enqueue, flush, getNotice, getQueue, patchCopies, resetQueue } from './feedbackQueue'
import { mockFetch } from './test/fetchMock'

const A = '0123456789abcdef'
const B = '1111111111111111'

/** 伺服器：GET /api/feedback/<uid> 回 server[uid]，POST 記下來 */
function serve(server: Record<string, string>, overrides: Record<string, () => { status?: number; json: unknown }> = {}) {
  return mockFetch({
    ...Object.fromEntries(Object.entries(server).map(([uid, mark]) => [`GET /api/feedback/${uid}`, () => ({ json: { uid, date: 'd', title: 't', mark } })])),
    'POST /api/feedback': (body) => ({ json: body }),
    ...overrides,
  })
}
const posts = (net: ReturnType<typeof mockFetch>) => net.calls.filter((c) => c.method === 'POST').map((c) => c.body)

beforeEach(() => {
  localStorage.removeItem('feedback-queue')
  resetQueue()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('排隊與合併', () => {
  it('同一則只留最後一次；before 記第一次按下前的', () => {
    enqueue(A, '+', '')
    enqueue(A, '-', '+') // 第二次按時畫面上是 +，但原本是 ''
    expect(getQueue()[A]).toMatchObject({ mark: '-', before: '' })
  })

  it('改回按下前的標記：整筆拿掉', () => {
    enqueue(A, '+', '')
    enqueue(A, '', '+')
    expect(getQueue()).toEqual({})
  })

  it('存在 localStorage：重開還在', () => {
    enqueue(A, '+', '')
    resetQueue()
    expect(Object.keys(getQueue())).toEqual([A])
  })
})

describe('補送', () => {
  it('伺服器沒改過：送出、清掉', async () => {
    enqueue(A, '+', '')
    const net = serve({ [A]: '' })
    expect(await flush()).toBe(1)
    expect(posts(net)).toEqual([{ uid: A, mark: '+' }])
    expect(getQueue()).toEqual({})
  })

  it('離線期間在別處改過：以伺服器為準，不送、提示', async () => {
    enqueue(A, '+', '')
    enqueue(B, '-', '')
    const net = serve({ [A]: '-', [B]: '' })
    expect(await flush()).toBe(1)
    expect(posts(net)).toEqual([{ uid: B, mark: '-' }])
    expect(getQueue()).toEqual({})
    expect(getNotice()).toMatch(/1 則標記在其他地方已經改過/)
  })

  it('4xx（例如 uid 不存在）：丟掉並提示', async () => {
    enqueue(A, '+', '')
    serve({}, { [`GET /api/feedback/${A}`]: () => ({ status: 404, json: { error: 'not found' } }) })
    await flush()
    expect(getQueue()).toEqual({})
    expect(getNotice()).toMatch(/補送失敗/)
  })

  it('5xx：保留、記次數；第 5 次還失敗就放棄', async () => {
    enqueue(A, '+', '')
    serve({ [A]: '' }, { 'POST /api/feedback': () => ({ status: 500, json: {} }) })
    for (let i = 1; i < 5; i++) {
      await flush()
      expect(getQueue()[A]?.tries).toBe(i)
    }
    await flush()
    expect(getQueue()).toEqual({})
    expect(getNotice()).toMatch(/放棄/)
  })

  it.each([
    ['登入逾時', 403],
    ['連不上', 502],
  ])('%s：整個保留，不發請求', async (_name, status) => {
    enqueue(A, '+', '')
    const net = serve({ [A]: '' }, { 'GET /api/heartbeat': () => ({ status, json: {} }) })
    expect(await flush()).toBe(0)
    expect(net.calls.map((c) => c.path)).toEqual(['/api/heartbeat'])
    expect(Object.keys(getQueue())).toEqual([A])
  })

  it('被導到登入頁（回應不是標記）：不送、不丟，算一次失敗', async () => {
    enqueue(A, '+', '')
    serve({}, { [`GET /api/feedback/${A}`]: () => ({ json: null }) })
    await flush()
    expect(getQueue()[A]?.tries).toBe(1)
  })

  it('佇列是空的：連探測都不做', async () => {
    const net = serve({})
    expect(await flush()).toBe(0)
    expect(net.calls).toEqual([])
  })
})

describe('patchCopies（本機副本）', () => {
  it('改寫有這則的 today／單日晨報，保留 ETag；沒有這則的不動', async () => {
    const store = new Map<string, Response>([
      ['/api/today', new Response(JSON.stringify({ report: { x: A }, marks: {} }), { headers: { ETag: '"t"' } })],
      ['/api/reports/2026-09-28', new Response(JSON.stringify({ report: { x: A }, marks: { [A]: '+' } }))],
      ['/api/reports/2026-09-27', new Response(JSON.stringify({ report: { x: B }, marks: {} }))],
      ['/api/reports', new Response(JSON.stringify({ reports: [A] }))],
    ])
    const put = vi.fn(async (req: Request, res: Response) => void store.set(new URL(req.url).pathname, res))
    vi.stubGlobal('caches', {
      open: async () => ({
        keys: async () => [...store.keys()].map((p) => new Request(`http://localhost${p}`)),
        match: async (req: Request) => store.get(new URL(req.url).pathname)?.clone(),
        put,
      }),
    })
    await patchCopies(A, '-')
    const today = store.get('/api/today') as Response
    expect(await today.clone().json()).toMatchObject({ marks: { [A]: '-' } })
    expect(today.headers.get('ETag')).toBe('"t"')
    await patchCopies(A, '')
    expect(await (store.get('/api/reports/2026-09-28') as Response).clone().json()).toMatchObject({ marks: {} })
    expect(put.mock.calls.map(([r]) => new URL(r.url).pathname)).not.toContain('/api/reports/2026-09-27')
    expect(put.mock.calls.map(([r]) => new URL(r.url).pathname)).not.toContain('/api/reports')
  })
})
