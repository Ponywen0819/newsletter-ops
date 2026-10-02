import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockFetch } from '../test/fetchMock'
import { report, UID_A, UID_B, UID_C } from '../test/fixtures'
import { ReportPanel, ReportView } from './ReportView'

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
    render(<ReportView report={report} marks={{}} onMark={() => {}} />)
    const first = screen.getByText('發生什麼：').closest('li')!
    const nested = within(first).getByText('細節').closest('li')!
    expect(first.querySelector(':scope > ul')).not.toBeNull()
    expect(nested.querySelector('.fb')).toBeNull() // 子項目沒有按鈕
    const background = screen.getByText('背景：B').closest('li')!
    expect(background.querySelector(':scope > .fb')).not.toBeNull()
    // 另一則清單的 mark、不在清單裡的獨立 mark：一共三組
    expect(document.querySelectorAll('.fb')).toHaveLength(3)
    // 項目有子清單時，按鈕在子清單「之後」（與 email 版型一致），而且仍是最外層項目的直接子節點
    const other = screen.getByText('其他').closest('li')!
    expect(other.lastElementChild).toHaveClass('fb')
    expect(other.lastElementChild!.previousElementSibling?.tagName).toBe('UL')
    const standalone = [...document.querySelectorAll('.fb')].filter((el) => !el.closest('li'))
    expect(standalone).toHaveLength(1)
  })

  it('每組按鈕亮的是自己那則的標記', () => {
    render(<ReportView report={report} marks={{ [UID_A]: '+', [UID_B]: '-' }} onMark={() => {}} />)
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
    render(<ReportPanel report={report} marks={{ [UID_B]: '-' }} />)
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
