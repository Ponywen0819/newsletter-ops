import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

  it('導覽有配色切換，每個頁面都有（包含找不到頁面）', async () => {
    open('/nope', {})
    await screen.findByRole('heading', { name: '找不到頁面' })
    expect(within(screen.getByRole('navigation')).getByRole('combobox', { name: '配色' })).toBeInTheDocument()
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
    ['/reports', 'reader'],
    ['/reports/2026-09-28', 'reader'],
    ['/auth', 'single'],
    [`/feedback/${UID_A}`, 'single'],
    ['/nope', 'single'],
  ])('%s → %s', (path, expected) => {
    open(path, { 'GET /api/today': never, 'GET /api/reports': never, 'GET /api/reports/2026-09-28': never, 'GET /api/auth': never, [`GET /api/feedback/${UID_A}`]: never })
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

  it('/reports：標題在左欄、日期列表在右欄；載入中與讀取失敗的外框一樣', async () => {
    const side = () => document.querySelector('.reader-side')
    const body = () => document.querySelector('.reader-body')
    open('/reports', { 'GET /api/reports': never })
    expect(body()).toHaveTextContent('載入中…')
    expect(side()).toBeEmptyDOMElement()
    cleanup()

    open('/reports', { 'GET /api/reports': () => ({ status: 500, json: { error: '壞了' } }) })
    expect(await screen.findByRole('heading', { name: '讀取失敗' })).toBeInTheDocument()
    expect(body()).toContainElement(screen.getByRole('heading', { name: '讀取失敗' }))
    cleanup()

    open('/reports', { 'GET /api/reports': () => ({ json: { reports: [{ date: '2026-09-28', headline: '某事發生' }] } }) })
    const link = await screen.findByRole('link', { name: '2026-09-28' })
    expect(side()).toContainElement(screen.getByRole('heading', { level: 1, name: '歷史晨報' }))
    expect(body()).toContainElement(link)
  })

  it('站內切換首頁 → 歷史 → 單日，外框一直是 reader；點到 single 的頁面才換', async () => {
    open('/', {
      'GET /api/today': () => ({ json: { date: '2026-09-28', latest: '2026-09-28', report, marks: {} } }),
      'GET /api/reports': () => ({ json: { reports: [{ date: '2026-09-28', headline: '某事發生' }] } }),
      'GET /api/reports/2026-09-28': () => ({ json: { date: '2026-09-28', report, marks: {} } }),
    }, true)
    const nav = within(await screen.findByRole('navigation'))
    expect(layout()).toBe('reader')
    await userEvent.click(nav.getByRole('link', { name: '歷史晨報' }))
    const link = await screen.findByRole('link', { name: '2026-09-28' })
    expect(layout()).toBe('reader')
    await userEvent.click(link)
    expect(await screen.findByRole('heading', { level: 1, name: '每日晨間簡報' })).toBeInTheDocument()
    expect(layout()).toBe('reader')
    await userEvent.click(await nav.findByRole('link', { name: 'Claude 授權' }))
    expect(layout()).toBe('single')
  })

  it('single 路由（找不到頁面）不畫兩欄', async () => {
    open('/nope', {})
    expect(await screen.findByRole('heading', { name: '找不到頁面' })).toBeInTheDocument()
    expect(document.querySelector('.reader')).toBeNull()
  })
})

describe('離線橫幅', () => {
  it('離線時標頭下方出現「離線中」，恢復連線後消失', async () => {
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
    open('/reports', { 'GET /api/reports': () => ({ json: { reports: [] } }) })
    await screen.findByRole('heading', { name: '歷史晨報' })
    expect(screen.queryByText(/離線中/)).toBeNull()

    act(() => {
      onLine.mockReturnValue(false)
      window.dispatchEvent(new Event('offline'))
    })
    const bar = screen.getByText(/離線中/)
    expect(bar).toHaveAttribute('role', 'status')
    expect(bar).toHaveTextContent('上次載入的內容')

    act(() => {
      onLine.mockReturnValue(true)
      window.dispatchEvent(new Event('online'))
    })
    await waitFor(() => expect(screen.queryByText(/離線中/)).toBeNull()) // online 之後要 heartbeat 通過才算恢復
    onLine.mockRestore()
  })

  it('一開始就離線：直接顯示橫幅；導覽與 /auth 連結的判斷不受影響', async () => {
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    open('/nope', {}, true)
    expect(screen.getByText(/離線中/)).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Claude 授權' })).toBeInTheDocument()
    onLine.mockRestore()
  })
})

describe('heartbeat 與快取', () => {
  const stale = { date: '2026-09-27', latest: '2026-09-27', report, marks: {} }
  const fresh = { ...stale, date: '2026-09-28', latest: '2026-09-28' }
  afterEach(() => vi.unstubAllGlobals())

  it('伺服器正常：載入一頁只多一次 heartbeat，而且不會因為什麼都沒變就重抓', async () => {
    const net = open('/', { 'GET /api/today': () => ({ json: fresh }) })
    await waitFor(() => expect(document.title).toBe('每日晨間簡報 2026-09-28'))
    act(() => void document.dispatchEvent(new Event('visibilitychange'))) // 回到前景：重探，但結果沒變
    await vi.waitFor(() => expect(net.calls.filter((c) => c.path === '/api/heartbeat').length).toBe(2))
    expect(net.calls.filter((c) => c.path === '/api/today')).toHaveLength(1)
    expect(net.calls.slice(0, 3).map((c) => c.path).filter((p) => p !== '/api/session')).toEqual(['/api/heartbeat', '/api/today'])
  })

  it('連不上：直接顯示快取；伺服器回來、回到前景後換成最新的', async () => {
    vi.stubGlobal('caches', { match: async (path: string) => (path === '/api/today' ? new Response(JSON.stringify(stale)) : undefined) })
    let up = false
    const net = open('/', {
      'GET /api/heartbeat': () => ({ status: up ? 200 : 502, json: { ok: true } }),
      'GET /api/today': () => ({ json: fresh }),
    })
    await waitFor(() => expect(document.title).toBe('每日晨間簡報 2026-09-27'))
    expect(await screen.findByText('連不上伺服器，顯示的是上次載入的內容')).toBeInTheDocument()
    expect(net.calls.some((c) => c.path === '/api/today')).toBe(false) // 沒有等資料請求逾時

    up = true
    act(() => void document.dispatchEvent(new Event('visibilitychange')))
    await waitFor(() => expect(document.title).toBe('每日晨間簡報 2026-09-28'))
    expect(document.querySelector('.offline-bar')).toBeEmptyDOMElement() // 晨報裡也有 status 區，所以用 class 找橫幅
  })

  it('登入逾時：不顯示快取，顯示「讀取失敗」與重新登入', async () => {
    vi.stubGlobal('caches', { match: async () => new Response(JSON.stringify(stale)) })
    open('/', { 'GET /api/heartbeat': () => ({ status: 403, json: {} }) })
    expect(await screen.findByRole('heading', { name: '讀取失敗' })).toBeInTheDocument()
    expect(screen.getByText('登入已逾時，請重新登入')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新登入' })).toBeInTheDocument()
    expect(document.title).not.toContain('2026-09-27')
  })
})
