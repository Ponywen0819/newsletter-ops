export type Theme = 'auto' | 'light' | 'dark'

const KEY = 'theme'

/** 唯一真相是 <html data-theme>：public/theme-init.js 在繪製前就依 localStorage 設好，這裡只讀 DOM。 */
export function readTheme(): Theme {
  const t = document.documentElement.dataset.theme
  return t === 'light' || t === 'dark' ? t : 'auto'
}

export function setTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'auto') delete root.dataset.theme
  else root.dataset.theme = theme
  try {
    if (theme === 'auto') localStorage.removeItem(KEY) // 自動 ＝ 沒有儲存的值
    else localStorage.setItem(KEY, theme)
  } catch {
    // 無痕模式／封鎖網站資料：這次照樣套用，只是下次不會記得
  }
}
