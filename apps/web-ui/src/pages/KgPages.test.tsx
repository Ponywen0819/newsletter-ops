import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { mockFetch } from '../test/fetchMock'
import { report, UID_A, UID_B } from '../test/fixtures'
import type { KgEntityPayload, KgEvent, KgItemPayload } from '../types'

const U1 = '3333333333333333'

function open(path: string, routes: Parameters<typeof mockFetch>[0]) {
  const net = mockFetch({ 'GET /api/session': () => ({ json: { local: false } }), ...routes })
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
  return net
}

const acme = { key: 'acme', name: 'Acme', type: 'org' as const }
const tsmc = { key: '台積電', name: '台積電', type: 'org' as const }

const events: KgEvent[] = [
  {
    id: 1,
    uid: U1,
    date: '2026-09-20',
    summary: 'Beta 否認收購',
    quote: 'not in talks',
    title: 'Beta denies',
    url: 'https://s2/b',
    source: 'S2',
    status: 'invalid',
    sources: 1,
    report: null,
    entities: [acme],
  },
  {
    id: 3,
    uid: UID_A,
    date: '2026-09-28',
    summary: 'Acme 完成收購',
    quote: 'Acme closed the deal',
    title: 'Acme closes',
    url: 'https://s1/a',
    source: 'S1',
    status: 'active',
    sources: 2,
    report: '2026-09-28',
    entities: [acme, tsmc],
  },
]

const item = (over: Partial<KgItemPayload> = {}): KgItemPayload => ({
  uids: [UID_A],
  items: [{ uid: UID_A, date: '2026-09-28', title: 'Acme closes', url: 'https://s1/a', source: 'S1', report: '2026-09-28' }],
  depth: 1,
  seeds: [3],
  events,
  links: [{ src: 3, dst: 1, kind: 'retract', quote: 'the denial was wrong', date: '2026-09-28' }],
  flags: {},
  truncated: false,
  ...over,
})

const detail = () => within(screen.getByRole('region', { name: '事件詳情' }))
const IGNORED = new Set(['/api/heartbeat', '/api/session', '/api/auth-health'])
const sent = (net: ReturnType<typeof mockFetch>) => net.calls.filter((c) => !IGNORED.has(c.path))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('知識圖譜', () => {
  it('晨報每則有「脈絡」連到這則的圖', async () => {
    open('/reports/2026-09-28', { 'GET /api/reports/2026-09-28': () => ({ json: { date: '2026-09-28', report, marks: {} } }) })
    const links = await screen.findAllByRole('link', { name: '脈絡' })
    expect(links.map((a) => a.getAttribute('href'))).toContain(`/kg/item/${UID_A}`)
    expect(links.every((a) => a.getAttribute('href')!.startsWith('/kg/item/'))).toBe(true)
  })

  it('歷史晨報的左欄連到 /kg', async () => {
    open('/reports', { 'GET /api/reports': () => ({ json: { reports: [] } }) })
    expect(await screen.findByRole('link', { name: '知識圖譜 →' })).toHaveAttribute('href', '/kg')
  })

  it('/kg/item：預設選中這則自己的事件；詳情有引句、關係、實體與晨報連結', async () => {
    open(`/kg/item/${UID_A}`, { [`GET /api/kg/item/${UID_A}?depth=1`]: () => ({ json: item() }) })
    expect(await screen.findByRole('heading', { level: 1, name: '新聞脈絡' })).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('脈絡：Acme closes'))
    expect(detail().getByRole('heading', { name: 'Acme 完成收購' })).toBeInTheDocument()
    expect(detail().getByText('Acme closed the deal')).toBeInTheDocument()
    expect(detail().getByText(/等 2 個來源/)).toBeInTheDocument()
    expect(detail().getByText('撤回／否認了')).toBeInTheDocument()
    expect(detail().getByText('the denial was wrong')).toBeInTheDocument()
    expect(detail().getByRole('link', { name: '台積電' })).toHaveAttribute('href', `/kg/entity/${encodeURIComponent('台積電')}`)
    expect(detail().getByRole('link', { name: '收錄於 2026-09-28 晨報' })).toHaveAttribute('href', '/reports/2026-09-28')
    expect(detail().queryByRole('link', { name: '以這則為中心' })).toBeNull() // 已經是中心
    expect(detail().getByRole('link', { name: 'Acme closes' })).toHaveAttribute('target', '_blank')
    // 圖只給滑鼠與觸控；鍵盤用列表（新到舊）
    expect(document.querySelector('.kg-canvas svg')).toHaveAttribute('aria-hidden', 'true')
    expect(document.querySelectorAll('.kg-node')).toHaveLength(2)
    expect(document.querySelector('.kg-node[data-seed][data-selected]')).not.toBeNull()
    const list = within(screen.getByRole('heading', { name: '事件列表' }).parentElement!)
    expect(list.getAllByRole('button').map((b) => b.textContent)).toEqual(['2026-09-28Acme 完成收購', '2026-09-20Beta 否認收購'])
    expect(list.getByText('已失效')).toBeInTheDocument()
  })

  it('/kg/item：從列表、關係或圖上選另一個事件；沒進晨報的不能標事實有誤', async () => {
    open(`/kg/item/${UID_A}`, { [`GET /api/kg/item/${UID_A}?depth=1`]: () => ({ json: item() }) })
    await screen.findByRole('heading', { name: '事件列表' })
    expect(detail().getByRole('button', { name: '事實有誤' })).toBeInTheDocument()
    await userEvent.click(detail().getByRole('button', { name: '2026-09-20：Beta 否認收購' }))
    expect(detail().getByRole('heading', { name: 'Beta 否認收購' })).toBeInTheDocument()
    expect(detail().getByText('被撤回／否認')).toBeInTheDocument()
    expect(detail().queryByRole('button', { name: '事實有誤' })).toBeNull()
    expect(detail().getByRole('link', { name: '以這則為中心' })).toHaveAttribute('href', `/kg/item/${U1}`)
    await userEvent.click(screen.getByRole('button', { name: '2026-09-28Acme 完成收購' }))
    expect(detail().getByRole('heading', { name: 'Acme 完成收購' })).toBeInTheDocument()
    await userEvent.click(document.querySelectorAll('.kg-node')[0]!)
    expect(detail().getByRole('heading', { name: 'Beta 否認收購' })).toBeInTheDocument()
  })

  it('/kg/item：換範圍重新載入；標了事實有誤也重新載入（狀態要重算），選取不跑掉', async () => {
    let flagged = false
    const net = open(`/kg/item/${UID_A},${UID_B}`, {
      [`GET /api/kg/item/${UID_A},${UID_B}?depth=1`]: () => ({ json: item({ uids: [UID_A, UID_B] }) }),
      [`GET /api/kg/item/${UID_A},${UID_B}?depth=2`]: () => ({
        json: item({ uids: [UID_A, UID_B], depth: 2, flags: flagged ? { [UID_A]: 'wrong' } : {} }),
      }),
      'POST /api/kg-flag': (body) => {
        flagged = true
        return { json: body }
      },
    })
    await screen.findByRole('heading', { name: '事件列表' })
    const range = within(screen.getByRole('group', { name: '範圍' }))
    expect(range.getByRole('button', { name: '直接相關' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(range.getByRole('button', { name: '再往外一層' }))
    await waitFor(() => expect(range.getByRole('button', { name: '再往外一層' })).toHaveAttribute('aria-pressed', 'true'))
    await userEvent.click(detail().getByRole('button', { name: '事實有誤' }))
    await waitFor(() => expect(sent(net).filter((c) => c.path.endsWith('depth=2'))).toHaveLength(2))
    expect(sent(net).find((c) => c.method === 'POST')!.body).toEqual({ uid: UID_A, flag: 'wrong' })
    await waitFor(() => expect(detail().getByRole('button', { name: '事實有誤' })).toHaveAttribute('aria-pressed', 'true'))
    expect(detail().getByRole('heading', { name: 'Acme 完成收購' })).toBeInTheDocument()
  })

  it('/kg/item：確認可信與事實有誤互斥，按另一個就換過去，再按一次取消', async () => {
    let flags: KgItemPayload['flags'] = { [UID_A]: 'wrong' }
    const net = open(`/kg/item/${UID_A}`, {
      [`GET /api/kg/item/${UID_A}?depth=1`]: () => ({ json: item({ flags }) }),
      'POST /api/kg-flag': (body) => {
        const { uid, flag } = body as { uid: string; flag: 'wrong' | 'trusted' | '' }
        flags = flag ? { [uid]: flag } : {}
        return { json: body }
      },
    })
    await screen.findByRole('heading', { name: '事件列表' })
    const trusted = () => detail().getByRole('button', { name: '確認可信' })
    const wrong = () => detail().getByRole('button', { name: '事實有誤' })
    expect(wrong()).toHaveAttribute('aria-pressed', 'true')
    expect(trusted()).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(trusted())
    await waitFor(() => expect(trusted()).toHaveAttribute('aria-pressed', 'true'))
    expect(wrong()).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(trusted())
    await waitFor(() => expect(trusted()).toHaveAttribute('aria-pressed', 'false'))
    expect(sent(net).filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([
      { uid: UID_A, flag: 'trusted' },
      { uid: UID_A, flag: '' },
    ])
  })

  it('/kg/item：沒有事件、找不到、其他錯誤', async () => {
    open(`/kg/item/${UID_A}`, { [`GET /api/kg/item/${UID_A}?depth=1`]: () => ({ json: item({ events: [], links: [], seeds: [] }) }) })
    expect(await screen.findByText(/沒有萃取出具體事件/)).toBeInTheDocument()
    cleanup()

    open(`/kg/item/${UID_B}`, {})
    expect(await screen.findByRole('heading', { name: '知識圖譜裡沒有資料' })).toBeInTheDocument()
    expect(screen.getByText(/這則新聞還沒被萃取成事件/)).toBeInTheDocument()
    cleanup()

    open(`/kg/item/${UID_B}`, { [`GET /api/kg/item/${UID_B}?depth=1`]: () => ({ status: 400, json: { error: 'depth 不合法' } }) })
    expect(await screen.findByRole('heading', { name: '讀取失敗' })).toBeInTheDocument()
    expect(screen.getByText('depth 不合法')).toBeInTheDocument()
  })

  it('/kg/entity：Unicode 的 key 編碼後送出；期間切換；常一起出現的實體', async () => {
    const key = encodeURIComponent('台積電')
    const payload = (days: number): KgEntityPayload => ({
      entity: { ...tsmc, total: 5 },
      days,
      since: '2026-06-30',
      related: [{ ...acme, count: 2 }],
      events,
      links: [],
      flags: {},
      truncated: days === 365,
    })
    const net = open(`/kg/entity/${key}`, {
      [`GET /api/kg/entity/${key}?days=90`]: () => ({ json: payload(90) }),
      [`GET /api/kg/entity/${key}?days=365`]: () => ({ json: payload(365) }),
    })
    expect(await screen.findByRole('heading', { level: 1, name: '台積電' })).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('台積電 — 知識圖譜'))
    expect(screen.getByText('組織 · 近 90 天 2 個事件（全部 5 個）')).toBeInTheDocument()
    const related = within(screen.getByRole('heading', { name: '常一起出現' }).parentElement!)
    expect(related.getByRole('link', { name: 'Acme' })).toHaveAttribute('href', '/kg/entity/acme')
    expect(detail().getByRole('heading', { name: 'Acme 完成收購' })).toBeInTheDocument() // 沒有中心：選最新的
    await userEvent.click(screen.getByRole('button', { name: '一年' }))
    expect(await screen.findByText('事件太多，只畫出其中一部分。')).toBeInTheDocument()
    expect(sent(net).map((c) => c.path)).toEqual([`/api/kg/entity/${key}?days=90`, `/api/kg/entity/${key}?days=365`])
  })

  it('/kg：近期常見的實體；搜尋改網址的 ?q=；還沒建立時說明', async () => {
    const entity = { ...tsmc, count: 3, last: '2026-09-28' }
    const net = open('/kg', {
      'GET /api/kg/entities?q=': () => ({ json: { q: '', available: true, since: '2026-08-29', entities: [entity] } }),
      [`GET /api/kg/entities?q=${encodeURIComponent('台積')}`]: () => ({ json: { q: '台積', available: true, since: null, entities: [] } }),
    })
    expect(await screen.findByRole('heading', { name: '近 30 天最常出現' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '台積電' })).toHaveAttribute('href', `/kg/entity/${encodeURIComponent('台積電')}`)
    expect(screen.getByText('組織 · 3 個事件 · 最近 2026-09-28')).toBeInTheDocument()
    await userEvent.type(screen.getByRole('searchbox', { name: '搜尋實體' }), '台積{Enter}')
    expect(await screen.findByText('沒有符合的實體。')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '「台積」的搜尋結果' })).toBeInTheDocument()
    expect(sent(net)).toHaveLength(2)
    cleanup()

    open('/kg', { 'GET /api/kg/entities?q=': () => ({ json: { q: '', available: false, since: '2026-08-29', entities: [] } }) })
    expect(await screen.findByText(/知識圖譜還沒建立/)).toBeInTheDocument()
  })
})
