import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { mockFetch } from '../test/fetchMock'
import type { AuthStatus } from '../types'

const TOKEN = 'sk-ant-oat01-' + 'Ab1'.repeat(14)
const missing: AuthStatus = { state: 'missing', env_token: false }
const saved = (over: Partial<Extract<AuthStatus, { tail: string }>> = {}): AuthStatus => ({
  state: 'ok', env_token: false, tail: 'Ab1', saved_at: '2026-10-02', expires_at: '2027-10-02', days_left: 365, ...over,
})

function open(routes: Parameters<typeof mockFetch>[0], local = true) {
  const net = mockFetch({ 'GET /api/session': () => ({ json: { local } }), ...routes })
  render(
    <MemoryRouter initialEntries={['/auth']}>
      <App />
    </MemoryRouter>,
  )
  return net
}

afterEach(() => vi.restoreAllMocks())

describe('/auth', () => {
  it('非本機：看不到授權頁，也沒有導覽連結，不會去問授權狀態', async () => {
    const net = open({}, false)
    expect(await screen.findByRole('heading', { name: '找不到頁面' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Claude 授權' })).toBeNull()
    expect(screen.queryByLabelText('OAuth token')).toBeNull()
    expect(net.calls.map((c) => c.path).filter((p) => p !== '/api/heartbeat')).toEqual(['/api/session'])
  })

  it('本機：顯示狀態與表單；token 欄位是密碼欄', async () => {
    open({ 'GET /api/auth': () => ({ json: missing }) })
    expect(await screen.findByText('尚未授權')).toBeInTheDocument()
    expect(screen.getByLabelText('OAuth token')).toHaveAttribute('type', 'password')
    expect(screen.getByRole('link', { name: 'Claude 授權' })).toBeInTheDocument()
    expect(document.title).toBe('Claude 授權')
  })

  it('環境有 token、頁面沒存：提示排程用的是環境變數', async () => {
    open({ 'GET /api/auth': () => ({ json: { state: 'missing', env_token: true } }) })
    expect(await screen.findByText(/排程若也設了同一個環境變數/)).toBeInTheDocument()
  })

  it('貼上有效 token：送出、狀態更新（只有尾碼）、清空輸入框', async () => {
    const net = open({
      'GET /api/auth': () => ({ json: missing }),
      'POST /api/auth/token': () => ({ json: { message: '驗證通過，已儲存', status: saved() } }),
    })
    await screen.findByText('尚未授權')
    await userEvent.type(screen.getByLabelText('OAuth token'), TOKEN)
    await userEvent.click(screen.getByRole('button', { name: '驗證並儲存' }))
    expect(await screen.findByText('驗證通過，已儲存')).toBeInTheDocument()
    expect(screen.getByText('已授權')).toBeInTheDocument()
    expect(screen.getByText('…Ab1')).toBeInTheDocument()
    expect(screen.getByLabelText('OAuth token')).toHaveValue('')
    expect(net.calls.find((c) => c.path === '/api/auth/token')?.body).toEqual({ token: TOKEN })
    expect(document.body.textContent).not.toContain(TOKEN)
  })

  it('驗證失敗：顯示原因、保留輸入、狀態不變', async () => {
    open({
      'GET /api/auth': () => ({ json: missing }),
      'POST /api/auth/token': () => ({ status: 422, json: { error: '授權失敗：401（token 可能貼錯、已撤銷或已過期）' } }),
    })
    await screen.findByText('尚未授權')
    await userEvent.type(screen.getByLabelText('OAuth token'), TOKEN)
    await userEvent.click(screen.getByRole('button', { name: '驗證並儲存' }))
    const msg = await screen.findByText(/授權失敗：401/)
    expect(msg).toHaveClass('auth-err')
    expect(screen.getByLabelText('OAuth token')).toHaveValue(TOKEN)
    expect(screen.getByText('尚未授權')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '驗證並儲存' })).toBeEnabled()
  })

  it('處理中：按鈕鎖住並提示會實際呼叫 Claude', async () => {
    let finish: (v: unknown) => void = () => {}
    open({
      'GET /api/auth': () => ({ json: saved() }),
      'POST /api/auth/test': () => new Promise((resolve) => (finish = resolve)) as never,
    })
    await screen.findByText('已授權')
    await userEvent.click(screen.getByRole('button', { name: '測試連線' }))
    expect(screen.getByText(/會實際呼叫一次 Claude/)).toBeInTheDocument()
    for (const name of ['測試連線', '驗證並儲存', '刪除已存的 token']) expect(screen.getByRole('button', { name })).toBeDisabled()
    finish({ json: { message: '連線正常', status: saved() } })
    expect(await screen.findByText('連線正常')).toHaveClass('auth-ok')
    expect(screen.getByRole('button', { name: '測試連線' })).toBeEnabled()
  })

  it('即將到期、已過期的提醒', async () => {
    open({ 'GET /api/auth': () => ({ json: saved({ state: 'expiring', days_left: 12 }) }) })
    const state = await screen.findByText(/即將到期（剩 12 天）/)
    expect(state).toHaveClass('auth-expiring')
  })

  it('刪除：要先確認；取消就不送出，確認才送出並回到尚未授權', async () => {
    const net = open({
      'GET /api/auth': () => ({ json: saved() }),
      'POST /api/auth/revoke': () => ({ json: { message: '已刪除儲存的 token', status: missing } }),
    })
    await screen.findByText('已授權')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false)
    await userEvent.click(screen.getByRole('button', { name: '刪除已存的 token' }))
    expect(confirm).toHaveBeenCalledOnce()
    expect(net.calls.some((c) => c.path === '/api/auth/revoke')).toBe(false)

    confirm.mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: '刪除已存的 token' }))
    expect(await screen.findByText('已刪除儲存的 token')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('尚未授權')).toBeInTheDocument())
    expect(within(document.querySelector('.auth-status') as HTMLElement).queryByText('Token')).toBeNull()
  })
})
