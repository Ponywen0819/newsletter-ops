import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { defineConfig } from 'vitest/config'

// 開發時 `npm run dev` 的 /api 轉給 Python 後端（web/server，newsletter-web）；埠號與後端共用 NEWSLETTER_WEB_PORT。
// 不改 Host、不加 X-Forwarded-*，後端才會把這裡當成本機（/auth 只服務本機）。
const backend = `http://127.0.0.1:${process.env.NEWSLETTER_WEB_PORT ?? 8787}`

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      // 自己寫 src/sw.ts（導覽要先走網路，見該檔說明）；外掛只負責 precache 清單、manifest 與註冊。
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'autoUpdate',
      // 'script'＝獨立的 registerSW.js：不是行內 <script>，CSP（default-src 'self'）不用放寬，main.tsx 也不用改
      injectRegister: 'script',
      // manifest 預設不帶 cookie，Cloudflare Access 會把它擋成登入頁；要 crossorigin="use-credentials"
      useCredentials: true,
      manifest: {
        id: '/',
        name: '每日晨間簡報',
        short_name: '晨報',
        description: '每天早上的科技與國際新聞簡報', // 不寫的話外掛會拿 package.json 的 description（開發者用的說明）
        lang: 'zh-Hant',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        theme_color: '#ffffff', // 標頭底色（styles.css 的 --card）
        background_color: '#f3f4f6', // --page
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
    }),
  ],
  server: {
    proxy: { '/api': backend },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
