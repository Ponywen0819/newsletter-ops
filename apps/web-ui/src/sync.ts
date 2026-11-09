import { check } from './connectivity'
import { cachedRecord, isRecord, PERIODIC_TAG, refresh, type SyncRecord } from './offlineCopy'

/** 頁面端的離線副本同步：先問連線（connectivity.ts），再交給 offlineCopy.ts；紀錄另存 localStorage 讓橫幅同步讀到。 */
export { KEEP, type SyncRecord } from './offlineCopy'

const MIN_GAP_MS = 10 * 60_000 // 回到前景時，距上次成功同步不到這麼久就不再同步
const RECORD_KEY = 'offline-sync' // localStorage：關掉 App 再離線開啟時，橫幅還說得出副本的新舊
const PERSIST_KEY = 'storage-persist-asked'
const PERIODIC_MS = 12 * 60 * 60_000 // 背景同步的最短間隔；實際頻率由瀏覽器依使用情況決定

export function lastSync(): SyncRecord | null {
  try {
    const rec: unknown = JSON.parse(localStorage.getItem(RECORD_KEY) ?? 'null')
    return isRecord(rec) ? rec : null
  } catch {
    return null
  }
}

function saveRecord(rec: SyncRecord): void {
  try {
    localStorage.setItem(RECORD_KEY, JSON.stringify(rec))
  } catch {
    // 私密視窗、被封鎖的儲存空間：橫幅退回不帶時間的文字
  }
}

/** service worker 在背景同步過的話，紀錄只在 cache 裡：比 localStorage 的新就搬過來。 */
export async function adoptBackgroundRecord(): Promise<void> {
  const rec = await cachedRecord()
  if (rec && rec.at > (lastSync()?.at ?? -Infinity)) saveRecord(rec)
}

/** 請瀏覽器別在空間吃緊時清掉副本。每台裝置只問一次（Firefox 會跳詢問），已經是持久的就不問；不支援或被拒絕都照舊。 */
export async function askPersist(): Promise<void> {
  const storage = typeof navigator === 'undefined' ? undefined : navigator.storage
  if (!storage?.persist || !storage.persisted) return
  try {
    if (localStorage.getItem(PERSIST_KEY) || (await storage.persisted())) return
    localStorage.setItem(PERSIST_KEY, '1')
    await storage.persist()
  } catch {
    // localStorage 被封鎖或 API 失敗：不重要，下次再說
  }
}

interface PeriodicSyncManager {
  register(tag: string, options?: { minInterval: number }): Promise<void>
}

/**
 * 沒開 App 時也讓瀏覽器定期喚醒 service worker 同步（Periodic Background Sync）。只有 Chromium 系、已安裝的 PWA 支援；
 * iOS Safari、Firefox 沒有。不支援、沒給權限都靜默略過。
 */
export async function registerPeriodicSync(): Promise<void> {
  try {
    const reg = await navigator.serviceWorker?.ready
    const periodic = (reg as (ServiceWorkerRegistration & { periodicSync?: PeriodicSyncManager }) | undefined)?.periodicSync
    if (!periodic) return
    const perm = await navigator.permissions?.query({ name: 'periodic-background-sync' as PermissionName })
    if (perm && perm.state !== 'granted') return
    await periodic.register(PERIODIC_TAG, { minInterval: PERIODIC_MS })
  } catch {
    // 不支援或被拒絕：和沒有這個功能一樣
  }
}

let syncedAt = -Infinity
let running: Promise<boolean> | null = null

async function run(): Promise<boolean> {
  if (typeof caches === 'undefined' || (await check()) !== 'ok') return false
  const rec = await refresh()
  if (rec === false) return false // 失敗保留舊副本
  syncedAt = Date.now()
  if (rec) {
    saveRecord(rec)
    void askPersist() // 有東西值得保留了才問
    void registerPeriodicSync()
  }
  return true
}

/** 同步一次；同時只跑一個。force＝不看間隔（啟動、恢復連線時）。回傳這次有沒有完成。 */
export function sync(force = false): Promise<boolean> {
  if (running) return running
  if (!force && Date.now() - syncedAt < MIN_GAP_MS) return Promise.resolve(false)
  running = run()
    .catch(() => false)
    .finally(() => {
      running = null
    })
  return running
}

/** 測試用：清掉上次同步的時間。 */
export function resetSync(): void {
  syncedAt = -Infinity
}
