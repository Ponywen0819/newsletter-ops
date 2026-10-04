import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockFetch } from '../test/fetchMock'
import { report, UID_A, UID_B, UID_C } from '../test/fixtures'
import type { Report } from '../types'
import { ReportPanel, ReportView } from './ReportView'

const UID_D = '3333333333333333'
const UID_E = '4444444444444444'
const t = (text: string) => [{ type: 'text' as const, text }]
// 主要新聞（段落＋清單）有回饋鈕；沒有前導段落的整張清單（「其餘收錄」）沒有——包含它的條目與其後的獨立 mark
const stories: Report = {
  ...report,
  blocks: [
    { type: 'heading', inline: t('科技') },
    { type: 'paragraph', inline: t('第一則') },
    { type: 'list', items: [{ inline: t('發生什麼：'), children: [{ inline: t('細節') }] }, { inline: t('背景：B'), uids: [UID_A] }] },
    { type: 'paragraph', inline: t('第二則') },
    { type: 'list', items: [{ inline: t('其他'), children: [{ inline: t('補充') }], uids: [UID_B] }] },
    { type: 'mark', uid: UID_C },
    { type: 'heading', inline: t('其餘收錄') },
    { type: 'list', items: [{ inline: t('單行 X'), uids: [UID_D] }] },
    { type: 'mark', uid: UID_E },
  ],
  sources: [],
}

describe('ReportView', () => {
  it('標題、日期、頭條、段落、資料來源', () => {
    render(<ReportView report={report} marks={{}} onMark={() => {}} />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('每日晨間簡報')
    expect(screen.getByText('2026/09/28')).toBeInTheDocument()
    expect(screen.getByText('今日頭條：').closest('.callout')).not.toBeNull()
    expect(screen.getByRole('heading', { name: '科技與 AI' })).toBeInTheDocument()
    const sources = within(screen.getByRole('heading', { name: '資料來源' }).nextElementSibling as HTMLElement)
    expect(sources.getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual(['https://a.example/x?a=1&b=2', 'https://a.example/1'])
  })

  it('巢狀清單；mark 在最外層項目的子清單之後，不在子項目裡', () => {
    render(<ReportView report={stories} marks={{}} onMark={() => {}} />)
    const first = screen.getByText('發生什麼：').closest('li')!
    const nested = within(first).getByText('細節').closest('li')!
    expect(first.querySelector(':scope > ul')).not.toBeNull()
    expect(nested.querySelector('.fb')).toBeNull() // 子項目沒有按鈕
    const background = screen.getByText('背景：B').closest('li')!
    expect(background.querySelector(':scope > .fb')).not.toBeNull()
    // 兩則新聞各一組、第二則後面的獨立 mark 一組：一共三組（「其餘收錄」那張清單沒有，見下一則測試）
    expect(document.querySelectorAll('.fb')).toHaveLength(3)
    // 項目有子清單時，按鈕在子清單「之後」（與 email 版型一致），而且仍是最外層項目的直接子節點
    const other = screen.getByText('其他').closest('li')!
    expect(other.lastElementChild).toHaveClass('fb')
    expect(other.lastElementChild!.previousElementSibling?.tagName).toBe('UL')
    const standalone = [...document.querySelectorAll('.fb')].filter((el) => !el.closest('li'))
    expect(standalone).toHaveLength(1)
  })

  it('「其餘收錄」（沒有前導段落的整張清單）不放有用／沒用：條目與其後的獨立 mark 都不畫', () => {
    render(<ReportView report={stories} marks={{ [UID_D]: '+', [UID_E]: '-' }} onMark={() => {}} />)
    expect(screen.getByText('單行 X')).toBeInTheDocument() // 內容還在
    const rest = screen.getByText('單行 X').closest('.unit')!
    expect(rest.querySelector('.fb, button')).toBeNull()
    expect(document.querySelectorAll('.unit .fb')).toHaveLength(3) // 其餘都在主要新聞裡
  })

  it('每組按鈕亮的是自己那則的標記', () => {
    render(<ReportView report={stories} marks={{ [UID_A]: '+', [UID_B]: '-' }} onMark={() => {}} />)
    const boxes = [...document.querySelectorAll<HTMLElement>('.fb')]
    const pressed = boxes.map((box) => [...box.querySelectorAll('[aria-pressed="true"]')].map((b) => b.getAttribute('data-mark')))
    expect(pressed).toEqual([['+'], ['-'], []]) // 背景：B（A）、其他（B）、獨立 mark（C）
  })

  it('沒有資料來源就不畫那一段', () => {
    render(<ReportView report={{ ...report, sources: [] }} marks={{}} onMark={() => {}} />)
    expect(screen.queryByRole('heading', { name: '資料來源' })).toBeNull()
  })
})

describe('ReportPanel', () => {
  it('按下去用伺服器的回應更新畫面：標記、覆蓋、取消；不影響其他則', async () => {
    let reply = { uid: UID_A, mark: '+' }
    mockFetch({ 'POST /api/feedback': () => ({ json: reply }) })
    render(<ReportPanel report={stories} marks={{ [UID_B]: '-' }} />)
    const mine = () => document.querySelectorAll<HTMLElement>('.fb')[0]!
    const pressedIn = (box: HTMLElement) => [...box.querySelectorAll('[aria-pressed="true"]')].map((b) => b.getAttribute('data-mark'))

    await userEvent.click(within(mine()).getByRole('button', { name: '有用' }))
    await within(mine()).findByRole('button', { name: '有用', pressed: true })
    expect(pressedIn(mine())).toEqual(['+'])

    reply = { uid: UID_A, mark: '-' }
    await userEvent.click(within(mine()).getByRole('button', { name: '沒用' }))
    await within(mine()).findByRole('button', { name: '沒用', pressed: true })
    expect(pressedIn(mine())).toEqual(['-'])

    reply = { uid: UID_A, mark: '' }
    await userEvent.click(within(mine()).getByRole('button', { name: '沒用' }))
    await within(mine()).findByRole('button', { name: '沒用', pressed: false })
    expect(pressedIn(mine())).toEqual([])
    expect(pressedIn(document.querySelectorAll<HTMLElement>('.fb')[1]!)).toEqual(['-']) // UID_B 沒動
    expect(UID_C).toBeTruthy()
  })
})

// fixture 的單位：s-3 重點 <標題>（段落＋清單，在「科技與 AI」下）、s-6 其餘收錄（清單＋獨立 mark）、s-sources 資料來源。
// 今日頭條不是單位，固定在最上面。內文永遠是一整頁往下捲；目錄只負責「讀到哪、跳過去」
describe('ReportView 筆電新聞目錄', () => {
  const scrolled: { el: HTMLElement; arg?: ScrollIntoViewOptions }[] = []

  beforeEach(() => {
    scrolled.length = 0
    Element.prototype.scrollIntoView = function (this: HTMLElement, arg?: boolean | ScrollIntoViewOptions) {
      scrolled.push({ el: this, arg: typeof arg === 'object' ? arg : undefined })
    }
  })
  afterEach(() => {
    window.history.replaceState(null, '', '/')
    Reflect.deleteProperty(window, 'matchMedia')
    Reflect.deleteProperty(document.documentElement, 'scrollHeight')
    Reflect.deleteProperty(Element.prototype, 'scrollIntoView')
    vi.useRealTimers()
  })

  const setup = () => render(<ReportView report={report} marks={{}} onMark={() => {}} />)
  const list = () => within(screen.getByRole('navigation', { name: '新聞目錄' }))
  const current = () => list().getByRole('link', { current: true }).textContent
  // 捲到的是哪裡：該分類的第一則會捲到分類標題（group:…），其餘捲到單位本身
  const scrolledTo = () => scrolled.map(({ el }) => el.dataset.unit ?? `group:${el.textContent}`)
  const setTops = (tops: Record<string, number>) => {
    for (const el of document.querySelectorAll<HTMLElement>('[data-unit]')) el.getBoundingClientRect = () => ({ top: tops[el.dataset.unit!] ?? 9999 }) as DOMRect
  }
  const scrollWindow = () => act(() => void window.dispatchEvent(new Event('scroll')))
  const tallPage = () => Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, value: 5000 })

  it('內文一整頁依序顯示：所有單位都在、沒有翻頁按鈕；目錄預設標在第一則', () => {
    setup()
    expect([...document.querySelectorAll<HTMLElement>('.unit')].map((u) => u.dataset.unit)).toEqual(['s-3', 's-6', 's-sources'])
    expect(document.querySelectorAll('[hidden], [data-active]')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /上一則|下一則/ })).toBeNull()
    expect(current()).toBe('重點 <標題>')
    expect(document.querySelectorAll('.fb')).toHaveLength(1) // 只有主要新聞（段落＋清單）有回饋鈕；「其餘收錄」那組沒有
    expect(scrolled).toHaveLength(0) // 沒有 hash 就不會自己捲
  })

  it('今日頭條固定在最上面：不是單位、不在目錄', () => {
    setup()
    const callout = screen.getByText('今日頭條：').closest('.callout')!
    expect(callout.closest('.unit')).toBeNull()
    expect(callout).toBe(document.querySelector('.reader-body')!.firstElementChild) // 在所有單位與分類標題之前
    expect(list().queryByRole('link', { name: /今日頭條/ })).toBeNull()
  })

  it('目錄：分類成組，資料來源在最後；項目是連到 #id 的連結', () => {
    setup()
    expect(list().getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['重點 <標題>', '#s-3'],
      ['其餘收錄', '#s-6'],
      ['資料來源', '#s-sources'],
    ])
    expect(list().getByText('科技與 AI').tagName).toBe('P') // 分組標題不是 heading，不會跟內文的 h2 撞名
  })

  it('點目錄：捲過去、亮起、更新 hash、不換頁；保留 router 的 history.state', async () => {
    const state = { usr: null, key: 'abc', idx: 0 }
    window.history.replaceState(state, '', '/')
    setup()
    await userEvent.click(list().getByRole('link', { name: '重點 <標題>' }))
    expect(scrolledTo()).toEqual(['group:科技與 AI']) // 該分類的第一則：捲到分類標題
    expect(scrolled[0]!.arg).toMatchObject({ block: 'start', behavior: 'auto' }) // 平順與否交給 CSS（尊重減少動態效果）
    await userEvent.click(list().getByRole('link', { name: '資料來源' }))
    expect(scrolledTo().at(-1)).toBe('s-sources')
    expect(current()).toBe('資料來源')
    expect(window.location.hash).toBe('#s-sources')
    expect(window.history.state).toEqual(state)
  })

  it('網址帶 hash 開頁：直接捲到那一則（instant）；認不得的 hash 不動', () => {
    window.history.replaceState(null, '', '/#s-6')
    setup()
    expect(scrolledTo()).toEqual(['group:其餘收錄'])
    expect(scrolled[0]!.arg).toMatchObject({ behavior: 'instant' })
    expect(current()).toBe('其餘收錄')
    cleanup()
    scrolled.length = 0
    window.history.replaceState(null, '', '/#nope')
    setup()
    expect(scrolled).toHaveLength(0)
    expect(current()).toBe('重點 <標題>')
  })

  it('hashchange（上一頁、手改網址）會跟著捲', () => {
    setup()
    window.history.replaceState(null, '', '/#s-sources')
    act(() => void window.dispatchEvent(new HashChangeEvent('hashchange')))
    expect(scrolledTo()).toEqual(['s-sources'])
    expect(current()).toBe('資料來源')
  })

  it('j／k 跳到下一則／上一則；頭尾不動；方向鍵不攔截（留給一般捲動）', async () => {
    setup()
    await userEvent.keyboard('k') // 第一則再往上：不動
    expect(scrolled).toHaveLength(0)
    await userEvent.keyboard('j')
    expect(current()).toBe('其餘收錄')
    await userEvent.keyboard('j')
    expect(current()).toBe('資料來源')
    await userEvent.keyboard('jj') // 最後一則再往下：不動
    expect(scrolledTo()).toEqual(['group:其餘收錄', 's-sources'])
    await userEvent.keyboard('k')
    expect(current()).toBe('其餘收錄')
    const before = scrolled.length
    await userEvent.keyboard('{ArrowDown}{ArrowUp}{ArrowDown}')
    expect(scrolled).toHaveLength(before)
    expect(window.location.hash).toBe('#s-6')
  })

  it('不攔截：輸入框裡打字、Ctrl／⌘／Alt 組合鍵、窄螢幕（matchMedia 不符）', async () => {
    setup()
    const input = document.body.appendChild(document.createElement('input'))
    input.focus()
    await userEvent.keyboard('jjj')
    input.remove()
    await userEvent.keyboard('{Control>}j{/Control}{Meta>}j{/Meta}{Alt>}j{/Alt}')
    window.matchMedia = (() => ({ matches: false })) as unknown as typeof window.matchMedia
    await userEvent.keyboard('j')
    expect(scrolled).toHaveLength(0)
    expect(current()).toBe('重點 <標題>')
  })

  it('焦點停在回饋鈕時 j 照樣跳（按完有用／沒用直接看下一則）', async () => {
    setup()
    document.querySelector<HTMLButtonElement>('.fb-btn')!.focus()
    await userEvent.keyboard('j')
    expect(current()).toBe('其餘收錄')
  })

  describe('scroll-spy：目錄跟著捲動亮起', () => {
    it('第一個開頭已經過「視窗上方 30%」那條線的單位', () => {
      setup()
      tallPage()
      setTops({ 's-3': -900, 's-6': 100, 's-sources': 900 }) // innerHeight 768 → 線在 230
      scrollWindow()
      expect(current()).toBe('其餘收錄')
      setTops({ 's-3': -1500, 's-6': -600, 's-sources': 200 })
      scrollWindow()
      expect(current()).toBe('資料來源')
      setTops({ 's-3': 400, 's-6': 800, 's-sources': 1200 }) // 回到最上面，還沒有任何單位過線：第一則
      scrollWindow()
      expect(current()).toBe('重點 <標題>')
    })

    it('捲到底一定是最後一則（最後幾則很短，捲到底也碰不到那條線）', () => {
      setup()
      Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, value: 700 }) // ≤ innerHeight
      setTops({ 's-3': -50, 's-6': -20, 's-sources': 600 })
      scrollWindow()
      expect(current()).toBe('資料來源')
    })

    it('點目錄後的捲動期間不改掉剛選的；之後恢復', async () => {
      vi.useFakeTimers({ toFake: ['Date'] })
      setup()
      tallPage()
      await userEvent.click(list().getByRole('link', { name: '資料來源' }))
      setTops({ 's-3': -100, 's-6': 100, 's-sources': 900 }) // 動畫途中經過其他單位
      scrollWindow()
      expect(current()).toBe('資料來源')
      vi.setSystemTime(Date.now() + 1000)
      scrollWindow()
      expect(current()).toBe('其餘收錄')
    })

    it('窄螢幕（matchMedia 不符）不更新', () => {
      setup()
      tallPage()
      window.matchMedia = (() => ({ matches: false })) as unknown as typeof window.matchMedia
      setTops({ 's-3': -900, 's-6': -500, 's-sources': 100 })
      scrollWindow()
      expect(current()).toBe('重點 <標題>')
    })
  })
})

describe('一則新聞掛兩個 uid（併了兩篇文章）', () => {
  const merged: Report = {
    ...report,
    blocks: [
      { type: 'heading', inline: [{ type: 'text', text: '商業' }] },
      { type: 'paragraph', inline: [{ type: 'text', text: '併了兩篇的新聞' }] },
      { type: 'list', items: [{ inline: [{ type: 'text', text: '後續觀察：…' }], uids: [UID_A, UID_B] }] },
    ],
    sources: [],
  }

  it('只畫一組回饋鈕，不是每個 uid 一組', () => {
    render(<ReportView report={merged} marks={{}} onMark={() => {}} />)
    expect(document.querySelectorAll('.fb')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: '有用' })).toHaveLength(1)
  })

  it('按下去兩個 uid 都標上，畫面亮起', async () => {
    mockFetch({ 'POST /api/feedback': (body) => ({ json: body }) })
    render(<ReportPanel report={merged} marks={{}} />)
    await userEvent.click(screen.getByRole('button', { name: '有用' }))
    await screen.findByRole('button', { name: '有用', pressed: true })
  })
})
