// 在 <head> 同步執行，第一次繪製前就把儲存的主題套到 <html>，避免先閃一下錯的顏色。
// 一定是外部檔：後端 CSP 是 default-src 'self'，不允許行內 <script>。邏輯與 src/theme.ts 的 setTheme 對應。
// 選了淺色／深色時，裝成 App 的狀態列也跟著換（兩個 theme-color 都改成那一色；顏色同 theme.ts 的 BAR）
try {
  const t = localStorage.getItem('theme')
  if (t === 'light' || t === 'dark') {
    document.documentElement.dataset.theme = t
    for (const meta of document.querySelectorAll('meta[name="theme-color"]'))
      meta.setAttribute('content', t === 'dark' ? '#1f2937' : '#ffffff')
  }
} catch {
  // 無痕模式／封鎖網站資料：當作「自動」
}
