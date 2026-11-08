import { afterEach, describe, expect, it, vi } from 'vitest'
import { readTheme, setTheme } from './theme'

const root = document.documentElement

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  delete root.dataset.theme
})

describe('setTheme', () => {
  it('light／dark：設 data-theme 並存進 localStorage', () => {
    setTheme('dark')
    expect(root.dataset.theme).toBe('dark')
    expect(localStorage.getItem('theme')).toBe('dark')
    setTheme('light')
    expect(root.dataset.theme).toBe('light')
    expect(localStorage.getItem('theme')).toBe('light')
  })

  it('auto：移除屬性與儲存的值（回到跟系統）', () => {
    setTheme('dark')
    setTheme('auto')
    expect(root.dataset.theme).toBeUndefined()
    expect(localStorage.getItem('theme')).toBeNull()
  })

  it('localStorage 不能用（丟例外）：不丟，這次照樣套用', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    expect(() => setTheme('dark')).not.toThrow()
    expect(root.dataset.theme).toBe('dark')
    expect(() => setTheme('auto')).not.toThrow()
    expect(root.dataset.theme).toBeUndefined()
  })
})

describe('readTheme', () => {
  it('沒有屬性、亂值 → auto；light／dark 原樣', () => {
    expect(readTheme()).toBe('auto')
    root.dataset.theme = 'evil'
    expect(readTheme()).toBe('auto')
    root.dataset.theme = 'dark'
    expect(readTheme()).toBe('dark')
    root.dataset.theme = 'light'
    expect(readTheme()).toBe('light')
  })
})
