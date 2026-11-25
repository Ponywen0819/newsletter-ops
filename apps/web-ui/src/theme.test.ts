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

describe('狀態列顏色（theme-color）', () => {
  const metas = () => [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map((m) => [m.media, m.content])
  const addMetas = () => {
    for (const [media, content] of [['(prefers-color-scheme: light)', '#ffffff'], ['(prefers-color-scheme: dark)', '#1f2937']]) {
      const meta = document.createElement('meta')
      meta.name = 'theme-color'
      meta.media = media!
      meta.content = content!
      document.head.append(meta)
    }
  }
  afterEach(() => document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove()))

  it('選固定的主題：兩個都換成那一色；自動：各自回到依系統的顏色', () => {
    addMetas()
    setTheme('dark')
    expect(metas()).toEqual([['(prefers-color-scheme: light)', '#1f2937'], ['(prefers-color-scheme: dark)', '#1f2937']])
    setTheme('light')
    expect(metas()).toEqual([['(prefers-color-scheme: light)', '#ffffff'], ['(prefers-color-scheme: dark)', '#ffffff']])
    setTheme('auto')
    expect(metas()).toEqual([['(prefers-color-scheme: light)', '#ffffff'], ['(prefers-color-scheme: dark)', '#1f2937']])
  })
})
