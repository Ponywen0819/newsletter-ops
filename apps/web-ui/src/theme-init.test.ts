import { afterEach, describe, expect, it, vi } from 'vitest'
// public/theme-init.js 是不經打包的 classic script，這裡把它當文字讀進來、在 jsdom 執行
import script from '../public/theme-init.js?raw'

const root = document.documentElement
const run = () => new Function(script)()

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  delete root.dataset.theme
})

describe('theme-init.js', () => {
  it.each(['light', 'dark'])('儲存的值是 %s → 套到 <html>', (theme) => {
    localStorage.setItem('theme', theme)
    run()
    expect(root.dataset.theme).toBe(theme)
  })

  it.each([null, 'evil', ''])('儲存的值是 %j → 不設屬性（自動）', (value) => {
    if (value !== null) localStorage.setItem('theme', value)
    run()
    expect(root.dataset.theme).toBeUndefined()
  })

  it('localStorage 不能用（丟例外）→ 不丟、不設屬性', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    expect(run).not.toThrow()
    expect(root.dataset.theme).toBeUndefined()
  })
})
