import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ mode }) => {
  // Vite 不會把 .env 檔放進 config 的 process.env，要自己 loadEnv。
  // 讀這個目錄的 .env／.env.local／.env.[mode]，只取 NEWSLETTER_ 開頭（不進前端 bundle）；shell 環境變數優先。
  const env = loadEnv(mode, import.meta.dirname, 'NEWSLETTER_')
  // 不改 Host、不加 X-Forwarded-*，後端才會把 dev server 當成本機
  const backend = process.env.NEWSLETTER_BACKEND_URL || env.NEWSLETTER_BACKEND_URL || 'http://127.0.0.1:8787'

  return {
    plugins: [
      react(),
      VitePWA({
        strategies: 'injectManifest',
        srcDir: 'src',
        filename: 'sw.ts',
        registerType: 'autoUpdate',
        // 獨立的 registerSW.js，CSP 不用放寬
        injectRegister: 'script',
        // manifest 預設不帶 cookie，Cloudflare Access 會把它擋成登入頁；要 crossorigin="use-credentials"
        useCredentials: true,
        manifest: {
          id: '/',
          name: '每日晨間簡報',
          short_name: '晨報',
          description: '每天早上的科技與國際新聞簡報',
          lang: 'zh-Hant',
          start_url: '/',
          scope: '/',
          display: 'standalone',
          theme_color: '#ffffff', // 標頭底色（styles/base.css 的 --card）
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
  }
})
