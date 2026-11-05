/** service worker（sw.ts）存 API 回應的 cache；頁面（api.ts）連不上時從同一個讀。獨立成檔：sw.ts 不能 import 會碰 window 的模組。 */
export const API_CACHE = 'api'
