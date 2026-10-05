import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mockFetch } from '../test/fetchMock'
import { ReportListPage } from './ReportListPage'

function open(reports: { date: string; headline: string }[]) {
  mockFetch({ 'GET /api/reports': () => ({ json: { reports } }) })
  return render(
    <MemoryRouter initialEntries={['/reports']}>
      <ReportListPage />
    </MemoryRouter>,
  )
}

const two = [
  { date: '2026-10-05', headline: '十月的第二則' },
  { date: '2026-10-02', headline: '十月的第一則' },
  { date: '2026-09-30', headline: '九月的' },
]

describe('ReportListPage 依月份分組', () => {
  it('跨兩個月以上：每個月份一個區塊（錨點 data-unit）、一個小標，日期連結在自己月份的區塊裡，新到舊', async () => {
    open(two)
    await screen.findByRole('link', { name: '2026-10-05' })
    const sections = [...document.querySelectorAll<HTMLElement>('.reader-body > section.unit')]
    expect(sections.map((s) => s.dataset.unit)).toEqual(['m-2026-10', 'm-2026-09'])
    expect(sections.map((s) => within(s).getByRole('heading', { level: 2 }).textContent)).toEqual(['2026-10', '2026-09'])
    expect(within(sections[0]!).getAllByRole('link').map((a) => a.textContent)).toEqual(['2026-10-05', '2026-10-02'])
    expect(within(sections[1]!).getAllByRole('link').map((a) => a.textContent)).toEqual(['2026-09-30'])
    expect(screen.getByRole('heading', { level: 1, name: '歷史晨報' })).toBeInTheDocument()
  })

  it('只有一個月份：和分組前完全一樣——一張 ul，沒有區塊、沒有小標', async () => {
    open(two.slice(0, 2))
    await screen.findByRole('link', { name: '2026-10-05' })
    expect(document.querySelector('section')).toBeNull()
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull()
    expect(document.querySelectorAll('.reader-body > ul.reports > li')).toHaveLength(2)
  })

  it('一份都沒有：提示照舊，沒有區塊', async () => {
    open([])
    expect(await screen.findByText('目前還沒有任何晨報。')).toBeInTheDocument()
    expect(document.querySelector('section')).toBeNull()
  })
})

describe('ReportListPage 月份目錄（≥1100px 左欄）', () => {
  const scrolled: { el: HTMLElement; arg?: ScrollIntoViewOptions }[] = []

  beforeEach(() => {
    scrolled.length = 0
    Element.prototype.scrollIntoView = function (this: HTMLElement, arg?: boolean | ScrollIntoViewOptions) {
      scrolled.push({ el: this, arg: typeof arg === 'object' ? arg : undefined })
    }
  })
  afterEach(() => {
    window.history.replaceState(null, '', '/')
    Reflect.deleteProperty(Element.prototype, 'scrollIntoView')
    Reflect.deleteProperty(document.documentElement, 'scrollHeight')
  })

  const nav = async () => within(await screen.findByRole('navigation', { name: '月份目錄' }))
  const current = async () => (await nav()).getByRole('link', { current: true }).getAttribute('href')
  const scrolledTo = () => scrolled.map(({ el }) => el.dataset.unit)

  it('跨兩個月以上：左欄標題下方有目錄，每月一項（月份＋份數），連到區塊的 #id；預設標在第一個月', async () => {
    open(two)
    const list = await nav()
    expect(list.getAllByRole('link').map((a) => [a.getAttribute('href'), a.textContent])).toEqual([
      ['#m-2026-10', '2026-10 2 份'],
      ['#m-2026-09', '2026-09 1 份'],
    ])
    expect(document.querySelector('.reader-side')).toContainElement(screen.getByRole('navigation', { name: '月份目錄' }))
    expect(await current()).toBe('#m-2026-10')
  })

  it('只有一個月份：沒有目錄', async () => {
    open(two.slice(0, 2))
    await screen.findByRole('link', { name: '2026-10-05' })
    expect(screen.queryByRole('navigation', { name: '月份目錄' })).toBeNull()
  })

  it('點月份：捲到該月區塊、亮起、更新 hash、不換頁；保留 router 的 history.state；修飾鍵照舊', async () => {
    const state = { usr: null, key: 'abc', idx: 0 }
    window.history.replaceState(state, '', '/')
    open(two)
    const list = await nav()
    await userEvent.click(list.getByRole('link', { name: /2026-09/ }))
    expect(scrolledTo()).toEqual(['m-2026-09'])
    expect(scrolled[0]!.arg).toMatchObject({ block: 'start', behavior: 'auto' })
    expect(await current()).toBe('#m-2026-09')
    expect(window.location.hash).toBe('#m-2026-09')
    expect(window.history.state).toEqual(state)
    fireEvent.click(list.getByRole('link', { name: /2026-10/ }), { ctrlKey: true }) // Ctrl＋點：新分頁開啟，不攔截
    expect(scrolledTo()).toEqual(['m-2026-09'])
    expect(await current()).toBe('#m-2026-09')
  })

  it('網址帶 hash 開頁：資料到了就直接捲到那個月（instant）並亮起', async () => {
    window.history.replaceState(null, '', '/#m-2026-09')
    open(two)
    expect(await current()).toBe('#m-2026-09')
    expect(scrolledTo()).toEqual(['m-2026-09'])
    expect(scrolled[0]!.arg).toMatchObject({ behavior: 'instant' })
  })

  it('scroll-spy：目前這個月跟著捲動亮起（細節由 ReportView 的測試覆蓋，這裡只驗接線）', async () => {
    open(two)
    await nav()
    Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, value: 5000 })
    const setTops = (tops: Record<string, number>) => {
      for (const el of document.querySelectorAll<HTMLElement>('[data-unit]')) el.getBoundingClientRect = () => ({ top: tops[el.dataset.unit!] ?? 9999 }) as DOMRect
    }
    setTops({ 'm-2026-10': -900, 'm-2026-09': 100 }) // innerHeight 768 → 線在 230
    act(() => void window.dispatchEvent(new Event('scroll')))
    expect(await current()).toBe('#m-2026-09')
    setTops({ 'm-2026-10': 50, 'm-2026-09': 900 })
    act(() => void window.dispatchEvent(new Event('scroll')))
    expect(await current()).toBe('#m-2026-10')
  })
})
