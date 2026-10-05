import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from './App'
import { mockFetch } from './test/fetchMock'
import { report, UID_A } from './test/fixtures'

function open(path: string, routes: Parameters<typeof mockFetch>[0], local = false) {
  const net = mockFetch({ 'GET /api/session': () => ({ json: { local } }), ...routes })
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
  return net
}

describe('路由', () => {
  it('/ 有當日晨報：畫出來，標題帶日期，標記來自伺服器', async () => {
    open('/', { 'GET /api/today': () => ({ json: { date: '2026-09-28', latest: '2026-09-28', report, marks: { [UID_A]: '+' } } }) })
    expect(await screen.findByRole('heading', { level: 1, name: '每日晨間簡報' })).toBeInTheDocument()
    expect(document.title).toBe('每日晨間簡報 2026-09-28')
    expect(document.querySelectorAll('[aria-pressed="true"]')).toHaveLength(1)
  })

  it('/ 當日還沒產出：說明並連到最新一份；一份都沒有就說沒有', async () => {
    open('/', { 'GET /api/today': () => ({ json: { date: '2026-10-03', latest: '2026-10-02', report: null, marks: {} } }) })
    expect(await screen.findByRole('heading', { name: '2026-10-03 的晨報還沒產出' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '2026-10-02' })).toHaveAttribute('href', '/reports/2026-10-02')
  })

  it('/ 一份晨報都沒有', async () => {
    open('/', { 'GET /api/today': () => ({ json: { date: '2026-10-03', latest: null, report: null, marks: {} } }) })
    expect(await screen.findByText('目前還沒有任何晨報。')).toBeInTheDocument()
  })

  it('/reports 列表：連到單日；沒有晨報時的提示', async () => {
    open('/reports', {
      'GET /api/reports': () => ({ json: { reports: [{ date: '2026-09-28', headline: '某事發生' }, { date: '2026-09-27', headline: '' }] } }),
      'GET /api/reports/2026-09-28': () => ({ json: { date: '2026-09-28', report, marks: {} } }),
    })
    const link = await screen.findByRole('link', { name: '2026-09-28' })
    expect(link.parentElement).toHaveTextContent('某事發生')
    await userEvent.click(link)
    expect(await screen.findByRole('heading', { level: 1, name: '每日晨間簡報' })).toBeInTheDocument()
  })

  it('/reports 空的', async () => {
    open('/reports', { 'GET /api/reports': () => ({ json: { reports: [] } }) })
    expect(await screen.findByText('目前還沒有任何晨報。')).toBeInTheDocument()
  })

  it('/reports/<date>：找不到 → 找不到頁面；報告壞了 → 顯示原因', async () => {
    open('/reports/2026-01-01', {})
    expect(await screen.findByRole('heading', { name: '找不到頁面' })).toBeInTheDocument()
  })

  it('/reports/<date>：報告格式不符時顯示伺服器給的原因', async () => {
    open('/reports/2026-09-26', {
      'GET /api/reports/2026-09-26': () => ({ status: 500, json: { error: '報告格式不符：找不到「> **今日頭條：** …」那一行' } }),
    })
    expect(await screen.findByRole('heading', { name: '讀取失敗' })).toBeInTheDocument()
    expect(screen.getByText(/報告格式不符：找不到/)).toBeInTheDocument()
  })

  it('不認得的路徑 → 找不到頁面，導覽仍在', async () => {
    open('/nope', {})
    expect(await screen.findByRole('heading', { name: '找不到頁面' })).toBeInTheDocument()
    const nav = within(screen.getByRole('navigation'))
    expect(nav.getByRole('link', { name: '今日晨報' })).toBeInTheDocument()
    expect(nav.getByRole('link', { name: '歷史晨報' })).toBeInTheDocument()
  })

  it('導覽有站名「晨報」，是純文字、不是連結（免得跟「今日晨報」重複）', async () => {
    open('/nope', {})
    await screen.findByRole('heading', { name: '找不到頁面' })
    const nav = within(screen.getByRole('navigation'))
    expect(nav.getByText('晨報')).toBeInTheDocument()
    expect(nav.queryByRole('link', { name: '晨報' })).toBeNull()
  })

  it('導覽的「Claude 授權」只在本機出現；問不到 session 時當作不是本機', async () => {
    open('/nope', {}, true)
    expect(await screen.findByRole('link', { name: 'Claude 授權' })).toBeInTheDocument()
  })

  it('後端連不上：session 失敗不影響晨報，只是沒有授權連結', async () => {
    open('/', { 'GET /api/session': () => ({ status: 502, json: {} }), 'GET /api/today': () => ({ json: { date: '2026-09-28', latest: null, report, marks: {} } }) })
    expect(await screen.findByRole('heading', { level: 1, name: '每日晨間簡報' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Claude 授權' })).toBeNull()
  })
})

// 外框（桌機寬度的標頭與卡片寬度）由路由決定，不看內容渲染了什麼：
// 內容還沒回來、出錯、找不到時外框也不能變，否則站內切換會跳
describe('版面由路由決定', () => {
  const layout = () => document.querySelector('[data-layout]')?.getAttribute('data-layout')
  const never = () => new Promise<never>(() => {}) // fetch 一直沒回來＝停在載入中

  it.each([
    ['/', 'reader'],
    ['/reports/2026-09-28', 'reader'],
    ['/auth', 'single'],
    [`/feedback/${UID_A}`, 'single'],
    ['/nope', 'single'],
  ])('%s → %s', (path, expected) => {
    open(path, { 'GET /api/today': never, 'GET /api/reports/2026-09-28': never, 'GET /api/auth': never, [`GET /api/feedback/${UID_A}`]: never })
    expect(layout()).toBe(expected)
  })

  it('reader 路由的載入中、讀取失敗、找不到、當日未產出，都在右欄（白底內文）裡', async () => {
    const body = () => document.querySelector('.reader-body')
    open('/', { 'GET /api/today': never })
    expect(body()).toHaveTextContent('載入中…')
    cleanup()

    open('/', { 'GET /api/today': () => ({ status: 500, json: { error: '壞了' } }) })
    expect(await screen.findByRole('heading', { name: '讀取失敗' })).toBeInTheDocument()
    expect(body()).toContainElement(screen.getByRole('heading', { name: '讀取失敗' }))
    cleanup()

    open('/', { 'GET /api/today': () => ({ json: { date: '2026-10-03', latest: null, report: null, marks: {} } }) })
    expect(await screen.findByRole('heading', { name: '2026-10-03 的晨報還沒產出' })).toBeInTheDocument()
    expect(body()).toContainElement(screen.getByRole('heading', { name: '2026-10-03 的晨報還沒產出' }))
    cleanup()

    open('/reports/2026-01-01', {})
    expect(await screen.findByRole('heading', { name: '找不到頁面' })).toBeInTheDocument()
    expect(body()).toContainElement(screen.getByRole('heading', { name: '找不到頁面' }))
  })

  it('single 路由（找不到頁面）不畫兩欄', async () => {
    open('/nope', {})
    expect(await screen.findByRole('heading', { name: '找不到頁面' })).toBeInTheDocument()
    expect(document.querySelector('.reader')).toBeNull()
  })
})
