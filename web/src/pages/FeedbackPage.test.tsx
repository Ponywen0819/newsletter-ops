import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { mockFetch } from '../test/fetchMock'
import type { FeedbackTarget, Mark } from '../types'

const UID = '0123456789abcdef'
const target = (mark: Mark = ''): FeedbackTarget => ({ uid: UID, date: '2026-09-28', title: '重點新聞', mark })

function open(query: string, routes: Parameters<typeof mockFetch>[0] = {}) {
  const net = mockFetch({
    'GET /api/session': () => ({ json: { local: false } }),
    [`GET /api/feedback/${UID}`]: () => ({ json: target() }),
    ...routes,
  })
  render(
    <MemoryRouter initialEntries={[`/feedback/${UID}${query}`]}>
      <App />
    </MemoryRouter>,
  )
  return net
}
const posts = (net: ReturnType<typeof mockFetch>) => net.calls.filter((c) => c.method === 'POST')

afterEach(() => vi.restoreAllMocks())

describe('/feedback/<uid>（email 連結的確認頁）', () => {
  it('只是開頁面不會寫入：顯示標題與確認鈕，沒有任何 POST', async () => {
    const net = open('?v=%2B')
    expect(await screen.findByText('「重點新聞」')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '確認標為 👍 有用' })).toBeEnabled()
    expect(posts(net)).toEqual([])
  })

  it('按確認才 POST；成功後改顯示已記下，按鈕消失', async () => {
    const net = open('?v=%2B', { 'POST /api/feedback': () => ({ json: { uid: UID, mark: '+' } }) })
    await userEvent.click(await screen.findByRole('button', { name: '確認標為 👍 有用' }))
    expect(await screen.findByText('已記下 👍 有用。')).toBeInTheDocument()
    expect(posts(net).map((c) => c.body)).toEqual([{ uid: UID, mark: '+' }])
    expect(screen.queryByRole('button', { name: /確認標為/ })).toBeNull()
    expect(screen.getByRole('link', { name: '回 2026-09-28 晨報' })).toHaveAttribute('href', '/reports/2026-09-28')
  })

  it('v=- 是 👎；手打的 ?v=+（被解成空白）當 👍', async () => {
    open('?v=-')
    expect(await screen.findByRole('button', { name: '確認標為 👎 沒用' })).toBeInTheDocument()
    document.body.innerHTML = ''
    open('?v=+')
    expect(await screen.findByRole('button', { name: '確認標為 👍 有用' })).toBeInTheDocument()
  })

  it('已經是同一個標記：只顯示已記下，沒有按鈕；不同的標記：說明會覆蓋', async () => {
    const same = open('?v=%2B', { [`GET /api/feedback/${UID}`]: () => ({ json: target('+') }) })
    expect(await screen.findByText('已記下 👍 有用。')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
    expect(posts(same)).toEqual([])
  })

  it('目前是 👎、信裡按 👍：說明會改成 👍，確認後送 +', async () => {
    const net = open('?v=%2B', {
      [`GET /api/feedback/${UID}`]: () => ({ json: target('-') }),
      'POST /api/feedback': () => ({ json: { uid: UID, mark: '+' } }),
    })
    expect(await screen.findByText('目前標記是 👎 沒用，確認後會改成 👍 有用。')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '確認標為 👍 有用' }))
    expect(await screen.findByText('已記下 👍 有用。')).toBeInTheDocument()
    expect(posts(net)[0].body).toEqual({ uid: UID, mark: '+' })
  })

  it('寫入失敗：顯示錯誤、按鈕還在可以重試，不會假裝成功', async () => {
    open('?v=%2B', { 'POST /api/feedback': () => ({ status: 500, json: { error: 'server error' } }) })
    await userEvent.click(await screen.findByRole('button', { name: '確認標為 👍 有用' }))
    expect(await screen.findByText(/儲存失敗/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '確認標為 👍 有用' })).toBeEnabled()
    expect(screen.queryByText(/已記下/)).toBeNull()
  })

  it('沒有 v、v 不認得：提示連結不完整，不去讀資料也不寫入', async () => {
    for (const query of ['', '?v=x']) {
      document.body.innerHTML = ''
      const net = open(query)
      expect(await screen.findByRole('heading', { name: '連結不完整' })).toBeInTheDocument()
      expect(posts(net)).toEqual([])
    }
  })

  it('uid 不在任何報告裡：找不到這則新聞', async () => {
    open('?v=%2B', { [`GET /api/feedback/${UID}`]: () => ({ status: 404, json: { error: 'not found' } }) })
    expect(await screen.findByRole('heading', { name: '找不到這則新聞' })).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('標題為空（沒有 curated 資料）：用「這則新聞」代替', async () => {
    open('?v=-', { [`GET /api/feedback/${UID}`]: () => ({ json: { ...target(), title: '' } }) })
    expect(await screen.findByText('「這則新聞」')).toBeInTheDocument()
  })
})
