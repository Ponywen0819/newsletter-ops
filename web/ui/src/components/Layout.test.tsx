import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { mockFetch } from '../test/fetchMock'
import { Layout } from './Layout'

const open = (heartbeatStatus: () => number) => {
  const net = mockFetch({ 'GET /api/heartbeat': () => ({ status: heartbeatStatus(), json: { ok: true } }) })
  render(
    <MemoryRouter>
      <Layout variant="single" />
    </MemoryRouter>,
  )
  return net
}
const heartbeats = (net: ReturnType<typeof mockFetch>) => net.calls.filter((c) => c.path === '/api/heartbeat').length

describe('離線橫幅', () => {
  it('伺服器正常：橫幅是空的（CSS 會把它藏起來），但仍是常駐的 status 區', async () => {
    const net = open(() => 200)
    await vi.waitFor(() => expect(heartbeats(net)).toBe(1))
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
  })

  it('連不上伺服器（網路還在）：說連不上伺服器，不是離線', async () => {
    open(() => 502)
    expect(await screen.findByText('連不上伺服器，顯示的是上次載入的內容')).toBeInTheDocument()
    expect(screen.queryByText(/離線中/)).not.toBeInTheDocument()
  })

  it('沒有網路：離線中，而且不探測', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    const net = open(() => 200)
    expect(await screen.findByText('離線中，顯示的是上次載入的內容')).toBeInTheDocument()
    expect(heartbeats(net)).toBe(0)
    vi.restoreAllMocks()
  })

  it('Access 登入逾時：提示並給「重新登入」按鈕', async () => {
    open(() => 403)
    expect(await screen.findByText('登入已逾時')).toBeInTheDocument()
    // 按鈕只是 location.reload()（jsdom 的 location 不能替換），實機行為在 PWA 驗收看
    expect(screen.getByRole('button', { name: '重新登入' })).toBeInTheDocument()
  })

  it('恢復後橫幅消失', async () => {
    let status = 502
    open(() => status)
    await screen.findByText(/連不上伺服器/)
    status = 200
    act(() => void window.dispatchEvent(new Event('online')))
    await vi.waitFor(() => expect(screen.getByRole('status')).toBeEmptyDOMElement())
  })
})
