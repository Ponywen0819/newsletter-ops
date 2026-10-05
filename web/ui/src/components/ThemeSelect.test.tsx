import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { ThemeSelect } from './ThemeSelect'

const root = document.documentElement

afterEach(() => {
  localStorage.clear()
  delete root.dataset.theme
})

describe('ThemeSelect', () => {
  it('沒有 data-theme → 顯示「自動」；有就反映出來', () => {
    const { unmount } = render(<ThemeSelect />)
    expect(screen.getByRole('combobox', { name: '配色' })).toHaveValue('auto')
    unmount()

    root.dataset.theme = 'dark'
    render(<ThemeSelect />)
    expect(screen.getByRole('combobox', { name: '配色' })).toHaveValue('dark')
  })

  it('改選項：data-theme 與 localStorage 都跟著變；選回「自動」就清掉', async () => {
    render(<ThemeSelect />)
    const select = screen.getByRole('combobox', { name: '配色' })

    await userEvent.selectOptions(select, '深色')
    expect(select).toHaveValue('dark')
    expect(root.dataset.theme).toBe('dark')
    expect(localStorage.getItem('theme')).toBe('dark')

    await userEvent.selectOptions(select, '淺色')
    expect(root.dataset.theme).toBe('light')
    expect(localStorage.getItem('theme')).toBe('light')

    await userEvent.selectOptions(select, '自動')
    expect(select).toHaveValue('auto')
    expect(root.dataset.theme).toBeUndefined()
    expect(localStorage.getItem('theme')).toBeNull()
  })
})
