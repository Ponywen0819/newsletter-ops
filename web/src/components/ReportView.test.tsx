import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { act } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
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

// fixture 的單位：s-3 重點 <標題>（段落＋清單）、s-6 其餘收錄（清單＋獨立 mark）、s-sources 資料來源。
// 今日頭條不是單位，固定在最上面
describe('ReportView 筆電閱讀器', () => {
  afterEach(() => {
    window.history.replaceState(null, '', '/')
    Reflect.deleteProperty(window, 'matchMedia')
    Reflect.deleteProperty(document.documentElement, 'scrollHeight')
  })

  const setup = () => render(<ReportView report={report} marks={{}} onMark={() => {}} />)
  const list = () => within(screen.getByRole('navigation', { name: '新聞清單' }))
  const activeUnit = () => document.querySelector<HTMLElement>('.unit[data-active]')?.dataset.unit
  const current = () => list().getByRole('link', { current: true }).textContent
  const counter = () => document.querySelector('.reader-pager span')!.textContent

  it('所有單位都在 DOM 裡（窄螢幕才會依序全部顯示），只有一個是 active，預設第一則新聞', () => {
    setup()
    expect([...document.querySelectorAll<HTMLElement>('.unit')].map((u) => u.dataset.unit)).toEqual(['s-3', 's-6', 's-sources'])
    expect(document.querySelectorAll('.unit[data-active]')).toHaveLength(1)
    expect(activeUnit()).toBe('s-3')
    expect(current()).toBe('重點 <標題>')
    expect(counter()).toBe('1 / 3')
    expect(document.querySelectorAll('.fb')).toHaveLength(3) // 分組沒有弄丟任何一組回饋鈕
  })

  it('今日頭條固定在最上面：不是單位、不在左清單、不隨切換消失', async () => {
    setup()
    const callout = screen.getByText('今日頭條：').closest('.callout')!
    expect(callout.closest('.unit')).toBeNull()
    expect(callout.parentElement).toHaveClass('reader-body')
    expect(callout).toBe(document.querySelector('.reader-body')!.firstElementChild) // 在所有單位與分類標題之前
    expect(list().queryByRole('link', { name: /今日頭條/ })).toBeNull()
    await userEvent.keyboard('j')
    expect(activeUnit()).toBe('s-6')
    expect(callout).toBeInTheDocument()
  })

  it('左清單：分類成組，資料來源在最後；項目是連到 #id 的連結', () => {
    setup()
    expect(list().getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['重點 <標題>', '#s-3'],
      ['其餘收錄', '#s-6'],
      ['資料來源', '#s-sources'],
    ])
    expect(list().getByText('科技與 AI').tagName).toBe('P') // 分組標題不是 heading，不會跟內文的 h2 撞名
  })

  it('點左清單項目：切換 active、更新 hash、不換頁；保留 router 的 history.state', async () => {
    const state = { usr: null, key: 'abc', idx: 0 }
    window.history.replaceState(state, '', '/')
    setup()
    await userEvent.click(list().getByRole('link', { name: '其餘收錄' }))
    expect(activeUnit()).toBe('s-6')
    expect(current()).toBe('其餘收錄')
    expect(window.location.hash).toBe('#s-6')
    expect(window.history.state).toEqual(state)
    expect(counter()).toBe('2 / 3')
  })

  it('網址 hash 指到哪一則就是哪一則；認不得的 hash 退回第一則；hashchange 會跟著切', () => {
    window.history.replaceState(null, '', '/#s-6')
    setup()
    expect(activeUnit()).toBe('s-6')
    cleanup()
    window.history.replaceState(null, '', '/#nope')
    setup()
    expect(activeUnit()).toBe('s-3')
    window.history.replaceState(null, '', '/#s-sources')
    act(() => void window.dispatchEvent(new HashChangeEvent('hashchange')))
    expect(activeUnit()).toBe('s-sources')
  })

  it('j／k 與 ↓／↑ 切換上一則下一則；頭尾不會壞', async () => {
    setup()
    await userEvent.keyboard('k') // 第一則再往上：不動
    expect(activeUnit()).toBe('s-3')
    await userEvent.keyboard('j')
    expect(activeUnit()).toBe('s-6')
    await userEvent.keyboard('{ArrowDown}')
    expect(activeUnit()).toBe('s-sources')
    await userEvent.keyboard('{ArrowUp}')
    expect(activeUnit()).toBe('s-6')
    await userEvent.keyboard('jjj') // 走到最後一則之後不再往下
    expect(activeUnit()).toBe('s-sources')
    expect(window.location.hash).toBe('#s-sources')
  })

  it('↑／↓ 只在頁面已捲到頭／尾才切換（長單位要能用方向鍵捲動）；j／k 不受影響', async () => {
    setup()
    Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, value: 5000 }) // 往下還能捲
    await userEvent.keyboard('{ArrowDown}')
    expect(activeUnit()).toBe('s-3')
    await userEvent.keyboard('j')
    expect(activeUnit()).toBe('s-6')
  })

  it('不攔截：輸入框裡打字、Ctrl／⌘／Alt 組合鍵、窄螢幕（matchMedia 不符）', async () => {
    setup()
    const input = document.body.appendChild(document.createElement('input'))
    input.focus()
    await userEvent.keyboard('jjj')
    expect(activeUnit()).toBe('s-3')
    input.remove()
    await userEvent.keyboard('{Control>}j{/Control}{Meta>}j{/Meta}{Alt>}j{/Alt}')
    expect(activeUnit()).toBe('s-3')
    window.matchMedia = (() => ({ matches: false })) as unknown as typeof window.matchMedia
    await userEvent.keyboard('j')
    expect(activeUnit()).toBe('s-3')
  })

  it('焦點停在回饋鈕時 j 照樣切換（按完有用／沒用直接看下一則）', async () => {
    setup()
    await userEvent.keyboard('j')
    expect(activeUnit()).toBe('s-6')
    document.querySelector<HTMLButtonElement>('.unit[data-active] .fb-btn')!.focus()
    await userEvent.keyboard('j')
    expect(activeUnit()).toBe('s-sources')
  })

  it('上一則／下一則按鈕：切換、頭尾 disabled', async () => {
    setup()
    const prev = screen.getByRole('button', { name: '上一則' })
    const next = screen.getByRole('button', { name: '下一則' })
    expect(prev).toBeDisabled()
    await userEvent.click(next)
    expect(activeUnit()).toBe('s-6')
    expect(prev).toBeEnabled()
    await userEvent.click(next)
    expect(activeUnit()).toBe('s-sources')
    expect(next).toBeDisabled()
    await userEvent.click(prev)
    expect(activeUnit()).toBe('s-6')
  })
})
