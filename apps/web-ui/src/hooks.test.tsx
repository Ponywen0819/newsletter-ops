import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useConnectivity, useFetch } from './hooks'
import { mockFetch } from './test/fetchMock'

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

describe('useFetch', () => {
  it('載入中 → 資料；失敗 → error', async () => {
    const ok = renderHook(() => useFetch(() => Promise.resolve('x'), []))
    expect(ok.result.current.loading).toBe(true)
    await waitFor(() => expect(ok.result.current).toEqual({ data: 'x', loading: false }))

    const bad = renderHook(() => useFetch(() => Promise.reject(new Error('壞了')), []))
    await waitFor(() => expect(bad.result.current.error?.message).toBe('壞了'))
    expect(bad.result.current.loading).toBe(false)
  })

  it('deps 變了以後，前一次較晚回來的結果不會蓋掉新的', async () => {
    const slow = deferred<string>()
    const { result, rerender } = renderHook(({ id }) => useFetch(() => (id === 1 ? slow.promise : Promise.resolve('第二次')), [id]), {
      initialProps: { id: 1 },
    })
    rerender({ id: 2 })
    await waitFor(() => expect(result.current.data).toBe('第二次'))
    await act(async () => slow.resolve('第一次（太晚）'))
    expect(result.current.data).toBe('第二次')
  })
})

describe('useConnectivity', () => {
  it('跟著 heartbeat 的結果與 online／offline 事件', async () => {
    let heartbeat = 502
    mockFetch({ 'GET /api/heartbeat': () => ({ status: heartbeat, json: { ok: true } }) })
    const { result } = renderHook(() => useConnectivity())
    expect(result.current).toBe('ok') // 還沒探測完：樂觀
    await waitFor(() => expect(result.current).toBe('unreachable'))

    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    act(() => void window.dispatchEvent(new Event('offline')))
    expect(result.current).toBe('offline')

    heartbeat = 200
    onLine.mockReturnValue(true)
    act(() => void window.dispatchEvent(new Event('online')))
    await waitFor(() => expect(result.current).toBe('ok'))
    onLine.mockRestore()
  })
})
