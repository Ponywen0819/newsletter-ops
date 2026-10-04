import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 開發時 `npm run dev` 的 /api 轉給 Python 後端（web/server，newsletter-web）；埠號與後端共用 NEWSLETTER_WEB_PORT。
// 不改 Host、不加 X-Forwarded-*，後端才會把這裡當成本機（/auth 只服務本機）。
const backend = `http://127.0.0.1:${process.env.NEWSLETTER_WEB_PORT ?? 8787}`

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': backend },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
