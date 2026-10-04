import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { mockFetch } from '../test/fetchMock'
import { UID_A, UID_B } from '../test/fixtures'
import type { Mark } from '../types'
import { FeedbackButtons } from './FeedbackButtons'

const up = () => screen.getByRole('button', { name: '有用' })
const down = () => screen.getByRole('button', { name: '沒用' })

function setup(mark: Mark = '', reply: () => { status?: number; json: unknown } = () => ({ json: { uid: UID_A, mark: '+' } })) {
  const net = mockFetch({ 'POST /api/feedback': reply })
  const onChange = vi.fn()
  render(<FeedbackButtons uids={[UID_A]} marks={mark ? { [UID_A]: mark } : {}} onChange={onChange} />)
  return { net, onChange }
}

describe('FeedbackButtons', () => {
  it('亮的是目前的標記', () => {
    setup('-')
    expect(down()).toHaveAttribute('aria-pressed', 'true')
    expect(up()).toHaveAttribute('aria-pressed', 'false')
  })

  it('按鈕是 aria-hidden 的 SVG 圖示、沒有文字（名稱靠 aria-label）', () => {
    setup('')
    for (const button of [up(), down()]) {
      expect(button.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(button.textContent).toBe('')
    }
  })

  it('沒標過 → 按「有用」送 +，畫面以伺服器回傳的為準', async () => {
    const { net, onChange } = setup('')
    await userEvent.click(up())
    expect(net.calls).toHaveLength(1)
    expect(net.calls[0]).toMatchObject({ method: 'POST', path: '/api/feedback', body: { uid: UID_A, mark: '+' } })
    expect(net.calls[0]!.headers['Content-Type']).toBe('application/json')
    expect(onChange).toHaveBeenCalledWith(UID_A, '+')
  })

  it('按已亮起的那顆＝取消（送空字串）', async () => {
    const { net, onChange } = setup('+', () => ({ json: { uid: UID_A, mark: '' } }))
    await userEvent.click(up())
    expect(net.calls[0]!.body).toEqual({ uid: UID_A, mark: '' })
    expect(onChange).toHaveBeenCalledWith(UID_A, '')
  })

  it('按另一顆＝覆蓋', async () => {
    const { net, onChange } = setup('+', () => ({ json: { uid: UID_A, mark: '-' } }))
    await userEvent.click(down())
    expect(net.calls[0]!.body).toEqual({ uid: UID_A, mark: '-' })
    expect(onChange).toHaveBeenCalledWith(UID_A, '-')
  })

  it('等伺服器回應時兩顆都鎖住，避免連按', async () => {
    let finish: (v: { json: unknown }) => void = () => {}
    const { onChange } = setup('', () => new Promise((resolve) => (finish = resolve)) as never)
    await userEvent.click(up())
    expect(up()).toBeDisabled()
    expect(down()).toBeDisabled()
    finish({ json: { uid: UID_A, mark: '+' } })
    await waitFor(() => expect(up()).toBeEnabled())
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('寫入失敗：顯示錯誤、不回報成功、可以再按', async () => {
    const { onChange } = setup('', () => ({ status: 500, json: { error: 'server error' } }))
    await userEvent.click(up())
    expect(await screen.findByText('儲存失敗，請重試；若一直失敗，重新整理頁面')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
    expect(up()).toBeEnabled()
    expect(up()).toHaveAttribute('aria-pressed', 'false')
  })
})

// 併了兩篇文章的新聞（後端為每篇各產一個 uid）：畫面上仍只有一組鈕，按下去對兩個 uid 一起投票
describe('FeedbackButtons：一則掛多個 uid', () => {
  const echo = (body: unknown) => ({ json: body })
  const setupMulti = (marks: Record<string, Mark> = {}, routes: Parameters<typeof mockFetch>[0] = { 'POST /api/feedback': echo }) => {
    const net = mockFetch(routes)
    const onChange = vi.fn()
    render(<FeedbackButtons uids={[UID_A, UID_B]} marks={marks} onChange={onChange} />)
    return { net, onChange }
  }

  it('只畫一組鈕；按下去對每個 uid 各送一次，各自回報', async () => {
    const { net, onChange } = setupMulti()
    expect(screen.getAllByRole('button', { name: '有用' })).toHaveLength(1)
    await userEvent.click(up())
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2))
    expect(net.calls.map((c) => c.body)).toEqual([
      { uid: UID_A, mark: '+' },
      { uid: UID_B, mark: '+' },
    ])
    expect(onChange).toHaveBeenCalledWith(UID_A, '+')
    expect(onChange).toHaveBeenCalledWith(UID_B, '+')
  })

  it('全部都是同一個標記才亮；再按＝全部取消', async () => {
    const { net } = setupMulti({ [UID_A]: '+', [UID_B]: '+' })
    expect(up()).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(up())
    await waitFor(() => expect(net.calls).toHaveLength(2))
    expect(net.calls.map((c) => c.body)).toEqual([
      { uid: UID_A, mark: '' },
      { uid: UID_B, mark: '' },
    ])
  })

  it('只標過其中一篇（例如信裡只按了一個）：兩顆都不亮；按下去統一成按的那個', async () => {
    const { net } = setupMulti({ [UID_A]: '+' })
    expect(up()).toHaveAttribute('aria-pressed', 'false')
    expect(down()).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(up()) // 不是「取消」：A 本來就是 +，但 B 還沒有，所以兩篇都送 +
    await waitFor(() => expect(net.calls).toHaveLength(2))
    expect(net.calls.map((c) => c.body)).toEqual([
      { uid: UID_A, mark: '+' },
      { uid: UID_B, mark: '+' },
    ])
  })

  it('兩篇標記不同：不亮；按下去統一', async () => {
    const { net } = setupMulti({ [UID_A]: '+', [UID_B]: '-' })
    expect(up()).toHaveAttribute('aria-pressed', 'false')
    expect(down()).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(down())
    await waitFor(() => expect(net.calls).toHaveLength(2))
    expect(net.calls.map((c) => c.body)).toEqual([
      { uid: UID_A, mark: '-' },
      { uid: UID_B, mark: '-' },
    ])
  })

  it('部分寫入失敗：只回報成功的那個，顯示錯誤，可以再按', async () => {
    const { onChange } = setupMulti({}, {
      'POST /api/feedback': (body) => ((body as { uid: string }).uid === UID_B ? { status: 500, json: { error: 'x' } } : echo(body)),
    })
    await userEvent.click(up())
    expect(await screen.findByText('儲存失敗，請重試；若一直失敗，重新整理頁面')).toBeInTheDocument()
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith(UID_A, '+') // B 沒寫成，畫面不能假裝成功
    expect(up()).toBeEnabled()
  })
})
