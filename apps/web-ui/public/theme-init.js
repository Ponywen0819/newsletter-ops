// 在 <head> 同步執行，第一次繪製前就把儲存的主題套到 <html>，避免先閃一下錯的顏色。
// 一定是外部檔：後端 CSP 是 default-src 'self'，不允許行內 <script>。邏輯與 src/theme.ts 的 setTheme 對應。
try {
  const t = localStorage.getItem('theme')
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t
} catch {
  // 無痕模式／封鎖網站資料：當作「自動」
}
