import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { mockFetch } from '../test/fetchMock'
import { UID_A, UID_B } from '../test/fixtures'
import type { Flags } from '../types'
import { FactFlagButton } from './FactFlagButton'

const button = () => screen.getByRole('button', { name: '事實有誤' })
const sent = (net: ReturnType<typeof mockFetch>) => net.calls.filter((c) => c.path !== '/api/heartbeat')

function setup(flags: Flags, uids = [UID_A], routes: Parameters<typeof mockFetch>[0] = {}) {
  const net = mockFetch({
    'POST /api/kg-flag': (body) => ({ json: body }),
    ...routes,
  })
  const onChange = vi.fn()
  render(<FactFlagButton uids={uids} flags={flags} onChange={onChange} />)
  return { net, onChange }
}

describe('FactFlagButton', () => {
  it('沒標過 → 送 wrong，每個 uid 各一次', async () => {
    const { net, onChange } = setup({}, [UID_A, UID_B])
    expect(button()).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(button())
    expect(sent(net).map((c) => c.body)).toEqual([
      { uid: UID_A, flag: 'wrong' },
      { uid: UID_B, flag: 'wrong' },
    ])
    expect(onChange).toHaveBeenCalledWith(UID_B, 'wrong')
  })

  it('已標 → 再按是取消', async () => {
    const { net, onChange } = setup({ [UID_A]: 'wrong' })
    expect(button()).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(button())
    expect(sent(net)[0]!.body).toEqual({ uid: UID_A, flag: '' })
    expect(onChange).toHaveBeenCalledWith(UID_A, '')
  })

  it('連不上時不送，直接說', async () => {
    const { net, onChange } = setup({}, [UID_A], { 'GET /api/heartbeat': () => ({ status: 503, json: {} }) })
    await userEvent.click(button())
    expect(sent(net)).toHaveLength(0)
    expect(onChange).not.toHaveBeenCalled()
    expect(await screen.findByRole('alert')).toHaveTextContent('連不上')
  })
})
