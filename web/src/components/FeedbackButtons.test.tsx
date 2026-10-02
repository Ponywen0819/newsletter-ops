import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { mockFetch } from '../test/fetchMock'
import { UID_A } from '../test/fixtures'
import type { Mark } from '../types'
import { FeedbackButtons } from './FeedbackButtons'

const up = () => screen.getByRole('button', { name: '有用' })
const down = () => screen.getByRole('button', { name: '沒用' })

function setup(mark: Mark = '', reply: () => { status?: number; json: unknown } = () => ({ json: { uid: UID_A, mark: '+' } })) {
  const net = mockFetch({ 'POST /api/feedback': reply })
  const onChange = vi.fn()
  render(<FeedbackButtons uid={UID_A} mark={mark} onChange={onChange} />)
  return { net, onChange }
}

describe('FeedbackButtons', () => {
  it('亮的是目前的標記', () => {
    setup('-')
    expect(down()).toHaveAttribute('aria-pressed', 'true')
    expect(up()).toHaveAttribute('aria-pressed', 'false')
  })

  it('沒標過 → 按 👍 送 +，畫面以伺服器回傳的為準', async () => {
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
    expect(await screen.findByText('儲存失敗，請重試')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
    expect(up()).toBeEnabled()
    expect(up()).toHaveAttribute('aria-pressed', 'false')
  })
})
