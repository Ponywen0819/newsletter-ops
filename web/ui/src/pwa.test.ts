import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { build } from 'vite'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { API_CACHE } from './cacheName'

// 鎖住「我們的設定」：真的跑一次 Vite build（輸出到暫存目錄，不動 dist/），檢查產物。
// Workbox 自己的行為（precache、NetworkFirst）是現成且有測試的，不在這裡重測。
// 這些設定壞了的後果都只在正式站（Cloudflare Access 後面）才看得到，本機測不出來，所以要在這裡擋。
const root = join(import.meta.dirname, '..')
let out = ''
const read = (file: string) => readFileSync(join(out, file), 'utf-8')

beforeAll(async () => {
  out = mkdtempSync(join(tmpdir(), 'pwa-test-'))
  await build({ root, mode: 'production', logLevel: 'silent', build: { outDir: out, emptyOutDir: true } })
}, 120_000)

afterAll(() => rmSync(out, { recursive: true, force: true }))

describe('manifest', () => {
  it('欄位齊全：可安裝、獨立視窗、主題色與標頭一致', () => {
    const m = JSON.parse(read('manifest.webmanifest'))
    expect(m).toMatchObject({
      name: '每日晨間簡報',
      short_name: '晨報',
      lang: 'zh-Hant',
      start_url: '/',
      scope: '/',
      display: 'standalone',
      theme_color: '#ffffff',
      background_color: '#f3f4f6',
    })
    // 外掛會偷用 package.json 的 description（開發者用的說明）：確認我們有明確設定
    expect(m.description).not.toMatch(/Vite|JSON API/)
  })

  it('icons：192、512（any）與 512（maskable），指到的檔案都存在、不是空的', () => {
    const { icons } = JSON.parse(read('manifest.webmanifest')) as { icons: { src: string; sizes: string; purpose?: string }[] }
    const has = (sizes: string, purpose = 'any') => icons.some((i) => i.sizes === sizes && (i.purpose ?? 'any') === purpose)
    expect(has('192x192') && has('512x512') && has('512x512', 'maskable')).toBe(true)
    for (const { src } of icons) expect(readFileSync(join(out, src.slice(1))).length, src).toBeGreaterThan(500)
  })
})

describe('index.html', () => {
  it('manifest 的 <link> 要帶 cookie（crossorigin="use-credentials"），否則 Access 會把它擋成登入頁', () => {
    expect(read('index.html')).toMatch(/<link rel="manifest"[^>]*crossorigin="use-credentials"/)
  })

  it('沒有行內 <script>／<style>（CSP 是 default-src \'self\'）；註冊用外部的 registerSW.js', () => {
    const html = read('index.html')
    for (const [tag] of html.matchAll(/<script\b[^>]*>/g)) expect(tag, tag).toMatch(/\bsrc=/)
    expect(html).not.toMatch(/<style\b/)
    expect(html).toContain('src="/registerSW.js"')
    expect(existsSync(join(out, 'registerSW.js'))).toBe(true)
  })

  it('theme-color 與 apple-touch-icon 在，圖示檔存在', () => {
    const html = read('index.html')
    expect(html).toMatch(/<meta name="theme-color" content="#ffffff"/)
    const icon = html.match(/<link rel="apple-touch-icon" href="([^"]+)"/)
    expect(icon).not.toBeNull()
    expect(existsSync(join(out, icon![1].slice(1)))).toBe(true)
  })
})

describe('sw.js', () => {
  // 壓縮後 regex 字面量的 `/` 會被跳脫成 `\/`，先拿掉反斜線再比對
  const source = () => read('sw.js').replaceAll('\\', '')

  it('precache 含 index.html（離線的 shell），但不含 sw.js 自己', () => {
    expect(source()).toMatch(/url:\s*"index\.html"|"url":\s*"index\.html"/)
    expect(source()).not.toMatch(/url:\s*"sw\.js"|"url":\s*"sw\.js"/)
  })

  it('只快取讀取用的 API（today／reports）', () => {
    expect(source()).toContain('api/(today|reports(/')
  })

  it('絕不碰會寫入、因請求而異、或必須打到網路的 API：auth、session、feedback、heartbeat', () => {
    expect(source()).not.toMatch(/api\/(auth|session|feedback|heartbeat)/)
  })

  it('頁面（api.ts）離線時讀的 cache 就是這裡寫的那個；NetworkFirst 有保險逾時', () => {
    expect(source()).toMatch(new RegExp(`cacheName:\\s*["'\`]${API_CACHE}["'\`]`)) // 壓縮後引號會變成反引號
    expect(source()).toMatch(/networkTimeoutSeconds:\s*\d/)
  })
})

describe('sw.ts 原始碼', () => {
  it('導覽路由排在 precache 的路由前面：否則 start_url `/` 永遠吃快取，Access 逾時的使用者出不去', () => {
    // 這個 bug 只有「Access 逾時」才會出現，本機與一般離線測試都看不到；建置後的 sw.js 被壓縮，順序不好看，所以直接看原始碼。
    const src = readFileSync(join(root, 'src/sw.ts'), 'utf-8')
    const nav = src.indexOf('new NavigationRoute')
    expect(nav).toBeGreaterThan(-1)
    expect(src).not.toContain('precacheAndRoute') // 它會在最前面就註冊 precache 路由
    expect(src.indexOf('addRoute()')).toBeGreaterThan(nav)
  })
})
