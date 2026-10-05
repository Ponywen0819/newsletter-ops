import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
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
