import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { check, getRecoveries, getStatus, subscribe } from './connectivity'

const fetchMock = vi.fn<typeof fetch>()
const respond = (status: number) => () => Promise.resolve(new Response('{}', { status }))
// jsdom 造不出 opaqueredirect 的 Response；瀏覽器在 redirect: 'manual' 遇到 302 時給的就是這個形狀
const opaqueRedirect = () => Promise.resolve({ type: 'opaqueredirect', status: 0, ok: false } as Response)
// 接受連線但不回應（lie-fi）：只有被 abort 才結束
const hang = (_: unknown, init?: RequestInit) =>
  new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))

beforeEach(() => {
  vi.useFakeTimers()
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('check：判定', () => {
  it.each([
    ['2xx', respond(200), 'ok'],
    ['502（Tunnel 在、origin 掛）', respond(502), 'unreachable'],
    ['網路錯誤', () => Promise.reject(new TypeError('Failed to fetch')), 'unreachable'],
    ['被 302 到別的來源（Access 逾時）', opaqueRedirect, 'login'],
    ['401', respond(401), 'login'],
    ['403', respond(403), 'login'],
  ] as const)('%s → %s', async (_name, reply, expected) => {
    fetchMock.mockImplementation(reply)
    expect(await check()).toBe(expected)
    expect(getStatus()).toBe(expected)
  })

  it('2 秒沒回應就算連不上（不等瀏覽器放棄）', async () => {
    fetchMock.mockImplementation(hang)
    const result = check()
    await vi.advanceTimersByTimeAsync(1999)
    expect(getStatus()).toBe('ok') // 還沒到
    await vi.advanceTimersByTimeAsync(1)
    expect(await result).toBe('unreachable')
  })

  it('navigator.onLine 是 false：直接 offline，不發請求', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    expect(await check()).toBe('offline')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('不吃 HTTP 快取、不跟著 redirect', async () => {
    fetchMock.mockImplementation(respond(200))
    await check()
    expect(fetchMock).toHaveBeenCalledWith('/api/heartbeat', expect.objectContaining({ cache: 'no-store', redirect: 'manual' }))
  })
})

describe('check：重用', () => {
  it('同時呼叫共用同一個探測；重用期（5 秒）內不重打，過了才重打', async () => {
    fetchMock.mockImplementation(respond(200))
    await Promise.all([check(), check(), check()])
    expect(fetchMock).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(4999)
    await check()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(1)
    await check()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('失敗的結果也重用：連續換頁不會每次都等逾時', async () => {
    fetchMock.mockImplementation(hang)
    const first = check()
    await vi.advanceTimersByTimeAsync(2000)
    await first
    expect(await check()).toBe('unreachable')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('subscribe：事件與恢復', () => {
  it('第一個訂閱者出現就探測一次；online、回到前景強制重探，hidden 與離開前景不算', async () => {
    fetchMock.mockImplementation(respond(200))
    const unsubscribe = subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2) // 在重用期內，但事件強制重探

    const visibility = vi.spyOn(document, 'visibilityState', 'get')
    visibility.mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    visibility.mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(3)

    unsubscribe()
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(3) // 沒有訂閱者就拆掉監聽
  })

  it('offline 事件立刻變 offline；online 後探測成功回到 ok，恢復計數 +1，訂閱者收到通知', async () => {
    fetchMock.mockImplementation(respond(200))
    const notify = vi.fn()
    const unsubscribe = subscribe(notify)
    await vi.advanceTimersByTimeAsync(0)
    expect(getRecoveries()).toBe(0)

    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    window.dispatchEvent(new Event('offline'))
    expect(getStatus()).toBe('offline')
    expect(getRecoveries()).toBe(0) // ok → 異常不算恢復

    onLine.mockReturnValue(true)
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(getStatus()).toBe('ok')
    expect(getRecoveries()).toBe(1)
    expect(notify).toHaveBeenCalledTimes(2) // offline、ok 各一次；沒變就不通知
    unsubscribe()
  })

  it('一直正常：恢復計數不動', async () => {
    fetchMock.mockImplementation(respond(200))
    await check(true)
    vi.advanceTimersByTime(10_000)
    await check()
    expect(getRecoveries()).toBe(0)
  })
})
