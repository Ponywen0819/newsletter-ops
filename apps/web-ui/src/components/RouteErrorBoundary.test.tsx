import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { isChunkError, RouteErrorBoundary } from './RouteErrorBoundary'

function Boom({ message }: { message: string }): never {
  throw new Error(message)
}

describe('RouteErrorBoundary', () => {
  afterEach(() => vi.restoreAllMocks())

  it('分包載不到（新版接手清掉舊檔）：說明網頁已更新，給重新載入', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {}) // React 與 componentDidCatch 都會印
    render(
      <RouteErrorBoundary>
        <Boom message="Failed to fetch dynamically imported module: /assets/KgPages-abc.js" />
      </RouteErrorBoundary>,
    )
    expect(screen.getByRole('heading', { name: '網頁已更新' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新載入' })).toBeInTheDocument()
  })

  it('其他錯誤：只換掉內容區，說畫面出錯', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <RouteErrorBoundary>
        <Boom message="x is undefined" />
      </RouteErrorBoundary>,
    )
    expect(screen.getByRole('heading', { name: '畫面出錯了' })).toBeInTheDocument()
  })

  it('沒出錯就照常畫', () => {
    render(
      <RouteErrorBoundary>
        <p>內容</p>
      </RouteErrorBoundary>,
    )
    expect(screen.getByText('內容')).toBeInTheDocument()
  })

  it('兩欄版面（reader）出錯：錯誤畫面放在 ReaderShell 裡，#content 還在', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <RouteErrorBoundary reader>
        <Boom message="x" />
      </RouteErrorBoundary>,
    )
    expect(document.getElementById('content')).toContainElement(screen.getByRole('heading', { name: '畫面出錯了' }))
  })

  it('isChunkError 認得各瀏覽器的訊息', () => {
    expect(isChunkError(new TypeError('Failed to fetch dynamically imported module: x'))).toBe(true) // Chrome
    expect(isChunkError(new TypeError('error loading dynamically imported module: x'))).toBe(true) // Firefox
    expect(isChunkError(new TypeError('Importing a module script failed.'))).toBe(true) // Safari
    expect(isChunkError(new TypeError('Failed to fetch'))).toBe(false) // 一般的 API 失敗不算
    expect(isChunkError('nope')).toBe(false)
  })
})
