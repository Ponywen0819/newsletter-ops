/// <reference lib="webworker" />
import { CacheableResponsePlugin } from 'workbox-cacheable-response'
import { clientsClaim } from 'workbox-core'
import { addRoute, cleanupOutdatedCaches, createHandlerBoundToURL, precache } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'
import { NetworkFirst } from 'workbox-strategies'
import { API_CACHE } from './cacheName'
import { PERIODIC_TAG, refresh } from './offlineCopy'

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: (string | { url: string; revision: string | null })[]
}

/** Periodic Background Sync 的事件（lib.webworker 還沒有這個型別） */
interface PeriodicSyncEvent extends ExtendableEvent {
  tag: string
}

precache(self.__WB_MANIFEST)
cleanupOutdatedCaches()

// 單一 bundle，新版立刻接手不會讓開著的頁面找不到檔案
void self.skipWaiting()
clientsClaim()

// 導覽一律先走網路：Access 逾時的 302 要交給瀏覽器去登入。只有離線才回 precache 的 index.html。
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

// 順序很重要：precache 的路由排在導覽路由前面的話，`/` 會永遠吃快取，Access 逾時的使用者出不去
addRoute()

// 只快取 today／reports，網路優先、只存 200。session、auth、feedback、heartbeat 絕不能進快取。
registerRoute(
  ({ sameOrigin, url }) => sameOrigin && /^\/api\/(today|reports(\/\d{4}-\d{2}-\d{2})?)$/.test(url.pathname),
  new NetworkFirst({
    cacheName: API_CACHE,
    networkTimeoutSeconds: 4,
    plugins: [new CacheableResponsePlugin({ statuses: [200] })],
  }),
)

// 沒開 App 時瀏覽器定期喚醒（頁面端 sync.ts 註冊；只有 Chromium 系已安裝的 PWA 會觸發）。和頁面同一套同步：
// 離線、連不上、Access 逾時的轉址都在 refresh() 裡被擋下，不寫入任何東西，也不通知。
self.addEventListener('periodicsync', ((event: PeriodicSyncEvent) => {
  if (event.tag === PERIODIC_TAG) event.waitUntil(refresh().then(() => undefined, () => undefined))
}) as EventListener)
