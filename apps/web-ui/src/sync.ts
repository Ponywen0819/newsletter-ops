import { API_CACHE } from './cacheName'
import { check } from './connectivity'
import type { ReportSummary } from './types'

/** 離線副本：線上時把今日、歷史列表與最近 KEEP 份晨報存進 API_CACHE（離線時 api.ts 讀同一個）。 */
export const KEEP = 30
const PARALLEL = 4
const MIN_GAP_MS = 10 * 60_000 // 回到前景時，距上次成功同步不到這麼久就不再同步
const RECORD_KEY = 'offline-sync' // localStorage：關掉 App 再離線開啟時，橫幅還說得出副本的新舊

/** 最近一次同步：at 是時間（ms），count 是副本裡的晨報份數，failed 是這次沒更新到（留著舊版或沒有）的份數。 */
export interface SyncRecord {
  at: number
  count: number
  failed: number
}

export function lastSync(): SyncRecord | null {
  try {
    const rec = JSON.parse(localStorage.getItem(RECORD_KEY) ?? 'null') as Partial<SyncRecord> | null
    const ok = rec && [rec.at, rec.count, rec.failed].every((v) => typeof v === 'number' && Number.isFinite(v))
    return ok ? (rec as SyncRecord) : null
  } catch {
    return null
  }
}

const PERSIST_KEY = 'storage-persist-asked'

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

function saveRecord(rec: SyncRecord): void {
  try {
    localStorage.setItem(RECORD_KEY, JSON.stringify(rec))
  } catch {
    // 私密視窗、被封鎖的儲存空間：橫幅退回不帶時間的文字
  }
}

let syncedAt = -Infinity
let running: Promise<boolean> | null = null

/**
 * 只存沒被轉址的 200：Access 逾時的 302（manual 下是 opaqueredirect）、錯誤頁都不能進副本。
 * 帶上副本的 ETag：伺服器回 304 就沿用舊副本，不傳整份（存著的 Response 本來就保留 headers）。
 */
async function store(cache: Cache, path: string): Promise<Response | null> {
  const old = await cache.match(path)
  const etag = old?.headers.get('ETag')
  const res = await fetch(path, { cache: 'no-store', redirect: 'manual', headers: etag ? { 'If-None-Match': etag } : {} })
  if (res.status === 304 && old) return old
  if (!res.ok || res.redirected) return null
  await cache.put(path, res.clone())
  return res
}

async function run(): Promise<boolean> {
  if (typeof caches === 'undefined' || (await check()) !== 'ok') return false
  const cache = await caches.open(API_CACHE)
  const list = await store(cache, '/api/reports').catch(() => null)
  if (!list) return false // 失敗保留舊副本
  const { reports } = (await list.json()) as { reports: ReportSummary[] }
  const keep = new Set(
    reports
      .toSorted((a, b) => b.date.localeCompare(a.date))
      .slice(0, KEEP)
      .map((r) => `/api/reports/${encodeURIComponent(r.date)}`),
  )
  const queue = ['/api/today', ...keep]
  const failed = new Set<string>()
  await Promise.all(
    Array.from({ length: PARALLEL }, async () => {
      for (let path = queue.shift(); path; path = queue.shift()) {
        if (!(await store(cache, path).catch(() => null))) failed.add(path)
      }
    }),
  )
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname
    if (/^\/api\/reports\/./.test(path) && !keep.has(path)) await cache.delete(req) // 只留最近 KEEP 份
  }
  syncedAt = Date.now()
  const missed = [...keep].filter((p) => failed.has(p))
  if (missed.length < keep.size || keep.size === 0) {
    // 「同步過」＝列表成功、而且至少寫入一份（一份都沒有晨報時也算）
    const cached = new Set((await cache.keys()).map((req) => new URL(req.url).pathname))
    saveRecord({ at: syncedAt, count: [...keep].filter((p) => cached.has(p)).length, failed: missed.length })
    void askPersist() // 有東西值得保留了才問
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
