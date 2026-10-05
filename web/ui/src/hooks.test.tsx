import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useFetch, useOnline } from './hooks'

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

describe('useOnline', () => {
  it('跟著 navigator.onLine 與 online／offline 事件', () => {
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
    const { result } = renderHook(() => useOnline())
    expect(result.current).toBe(true)

    act(() => {
      onLine.mockReturnValue(false)
      window.dispatchEvent(new Event('offline'))
    })
    expect(result.current).toBe(false)

    act(() => {
      onLine.mockReturnValue(true)
      window.dispatchEvent(new Event('online'))
    })
    expect(result.current).toBe(true)
    onLine.mockRestore()
  })
})
