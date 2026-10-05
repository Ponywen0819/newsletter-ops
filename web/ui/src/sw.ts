/// <reference lib="webworker" />
import { CacheableResponsePlugin } from 'workbox-cacheable-response'
import { clientsClaim } from 'workbox-core'
import { addRoute, cleanupOutdatedCaches, createHandlerBoundToURL, precache } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'
import { NetworkFirst } from 'workbox-strategies'
import { API_CACHE } from './cacheName'

// __WB_MANIFEST 由 vite-plugin-pwa 在建置時換成 precache 清單（index.html、/assets/* …，檔名帶內容 hash）
declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: (string | { url: string; revision: string | null })[]
}

precache(self.__WB_MANIFEST)
cleanupOutdatedCaches() // 新版啟用時，舊版的 precache 自動清掉

// 新版立刻接手，不等舊視窗關掉。前端是單一 bundle，開著的頁面已全部載入，換版不會讓它找不到檔案。
void self.skipWaiting()
clientsClaim()

// 導覽一律先走網路：Cloudflare Access 逾時時回的 302 要原樣交給瀏覽器去登入，不能拿快取的 shell 蓋掉。
// 只有網路錯誤（離線）才回 precache 的 index.html，由前端路由接手（任何前端路徑共用同一份）。
// 導覽的回應不進任何 runtime cache，所以登入頁（redirect／非 2xx）不可能被存起來。
const shell = createHandlerBoundToURL('/index.html')
registerRoute(
  new NavigationRoute(
    async (options) => {
      try {
        return await fetch(options.request)
      } catch {
        return shell(options)
      }
    },
    { denylist: [/^\/api\//] },
  ),
)

// 順序很重要：路由先註冊的先贏。precache 的路由會把 `/` 對應到 index.html（directoryIndex），
// 若它排在導覽路由前面，start_url `/` 就永遠吃快取、碰不到網路，Access 逾時的使用者出不去。
// 所以用 precache()＋addRoute() 拆開，讓上面的導覽路由排前面；precache 的路由只剩子資源（/assets/*、icons）。
addRoute()

// 只快取讀取用的 API：有網路永遠拿最新（標記狀態會變），離線讀上次的；只存 200。
// 其餘路徑一律不註冊 → 不經過 SW：POST、/api/session（local 因請求而異）、/api/auth*（能寫入憑證）、/api/feedback/*，
// 還有 /api/heartbeat（連線探測，一定要打到網路，不能被快取）。新增路由前先確認不會碰到這些。
// 連不連得上由頁面先用 heartbeat 判斷（connectivity.ts、api.ts）：連不上時頁面直接讀這個 cache，不會走到這裡。
// 這裡的逾時只是保險：heartbeat 通過後、請求途中才斷線（或變很慢）時，不用等瀏覽器自己放棄；有快取才會在逾時後回快取。
registerRoute(
  ({ sameOrigin, url }) => sameOrigin && /^\/api\/(today|reports(\/\d{4}-\d{2}-\d{2})?)$/.test(url.pathname),
  new NetworkFirst({
    cacheName: API_CACHE,
    networkTimeoutSeconds: 4,
    plugins: [new CacheableResponsePlugin({ statuses: [200] })],
  }),
)
