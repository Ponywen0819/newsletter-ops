export type Theme = 'auto' | 'light' | 'dark'

const KEY = 'theme'
// 頁首底色（styles/base.css 的 --card）：裝成 App 時的狀態列／標題列顏色。public/theme-init.js 也寫了同樣的值
const BAR = { light: '#ffffff', dark: '#1f2937' } as const

/** index.html 有兩個 theme-color（依系統的淺色／深色）。選了固定的主題就兩個都改成那一色，自動就各回各的。 */
function applyBarColor(theme: Theme): void {
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    const scheme = meta.media.includes('dark') ? 'dark' : 'light'
    meta.content = BAR[theme === 'auto' ? scheme : theme]
  }
}

/** 唯一真相是 <html data-theme>：public/theme-init.js 在繪製前就依 localStorage 設好，這裡只讀 DOM。 */
export function readTheme(): Theme {
  const t = document.documentElement.dataset.theme
  return t === 'light' || t === 'dark' ? t : 'auto'
}

export function setTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'auto') delete root.dataset.theme
  else root.dataset.theme = theme
  applyBarColor(theme)
  try {
    if (theme === 'auto') localStorage.removeItem(KEY) // 自動 ＝ 沒有儲存的值
    else localStorage.setItem(KEY, theme)
  } catch {
    // 無痕模式／封鎖網站資料：這次照樣套用，只是下次不會記得
  }
}
