import { act, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthHealthProvider } from '../authHealth'
import { SessionProvider } from '../session'
import { mockFetch } from '../test/fetchMock'
import type { AuthHealth } from '../types'
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

describe('授權橫幅', () => {
  const health = (state: AuthHealth['state'], kind: string): AuthHealth => ({ state, kind, checked_at: '2026-11-02T01:00:00+00:00' })
  const openHealth = (reply: AuthHealth | null, local = true, at = '/') => {
    const net = mockFetch({
      'GET /api/session': () => ({ json: { local } }),
      'GET /api/auth-health': () => (reply ? { json: reply } : { status: 500, json: { error: 'x' } }),
    })
    const view = render(
      <MemoryRouter initialEntries={[at]}>
        <SessionProvider>
          <AuthHealthProvider>
            <Layout variant="single" />
          </AuthHealthProvider>
        </SessionProvider>
      </MemoryRouter>,
    )
    const bar = () => view.container.querySelector('.auth-bar')
    const settled = () => vi.waitFor(() => expect(net.calls.some((c) => c.path === '/api/auth-health')).toBe(true))
    return { net, bar, settled }
  }

  it('授權失效：本機給「前往授權」連結', async () => {
    const { bar } = openHealth(health('invalid', 'auth'))
    expect(await screen.findByText(/Claude 授權已失效，下次晨報會失敗/)).toBeInTheDocument()
    expect(bar()).toHaveClass('auth-bar-bad')
    expect(within(bar() as HTMLElement).getByRole('link', { name: '前往授權' })).toHaveAttribute('href', '/auth')
  })

  it('已經在 /auth：橫幅不再放連結', async () => {
    const { bar } = openHealth(health('invalid', 'auth'), true, '/auth')
    await screen.findByText(/Claude 授權已失效/)
    expect(within(bar() as HTMLElement).queryByRole('link')).toBeNull()
  })

  it('經 Tunnel（非本機）：說要回主機授權，沒有連結', async () => {
    const { bar } = openHealth(health('invalid', 'auth'), false)
    expect(await screen.findByText(/請在主機上重新授權/)).toBeInTheDocument()
    expect(within(bar() as HTMLElement).queryByRole('link')).toBeNull()
  })

  it.each([
    ['missing', 'missing', /尚未授權 Claude/, 'auth-bar-bad'],
    ['expiring', 'ok', /快到期/, 'auth-bar-warn'],
    ['invalid', 'api_key', /API key/, 'auth-bar-bad'],
    ['unknown', 'quota', /額度用完/, 'auth-bar-warn'],
    ['unknown', 'timeout', /暫時無法確認/, 'auth-bar-warn'],
  ] as const)('%s／%s：顯示提醒', async (state, kind, text, cls) => {
    const { bar } = openHealth(health(state, kind))
    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(bar()).toHaveClass(cls)
  })

  it('額度用完、暫時無法確認：不是授權問題，不叫人去重新授權', async () => {
    const { bar } = openHealth(health('unknown', 'quota'))
    await screen.findByText(/額度用完/)
    expect(within(bar() as HTMLElement).queryByRole('link')).toBeNull()
    expect(bar()).not.toHaveTextContent('重新授權')
  })

  it.each([
    ['正常', health('ok', 'ok')],
    ['伺服器剛啟動、還沒驗證過', { state: 'unknown', kind: 'unchecked', checked_at: null } as AuthHealth],
    ['問不到狀態', null],
  ])('%s：沒有橫幅', async (_name, reply) => {
    const { bar, settled } = openHealth(reply)
    await settled()
    await vi.waitFor(() => expect(bar()).toBeNull())
    expect(screen.getByRole('status')).toBeEmptyDOMElement() // 連線橫幅不受影響
  })

  it('回到這個分頁時重新讀取', async () => {
    const { net, settled } = openHealth(health('ok', 'ok'))
    await settled()
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await vi.waitFor(() => expect(net.calls.filter((c) => c.path === '/api/auth-health')).toHaveLength(2))
  })
})
