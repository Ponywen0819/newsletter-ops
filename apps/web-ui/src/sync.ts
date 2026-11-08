import { API_CACHE } from './cacheName'
import { check } from './connectivity'
import type { ReportSummary } from './types'

/** 離線副本：線上時把今日、歷史列表與最近 KEEP 份晨報存進 API_CACHE（離線時 api.ts 讀同一個）。 */
export const KEEP = 30
const PARALLEL = 4
const MIN_GAP_MS = 10 * 60_000 // 回到前景時，距上次成功同步不到這麼久就不再同步

let syncedAt = -Infinity
let running: Promise<boolean> | null = null

/** 只存沒被轉址的 200：Access 逾時的 302（manual 下是 opaqueredirect）、錯誤頁都不能進副本。 */
async function store(cache: Cache, path: string): Promise<Response | null> {
  const res = await fetch(path, { cache: 'no-store', redirect: 'manual' })
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
  await Promise.all(
    Array.from({ length: PARALLEL }, async () => {
      for (let path = queue.shift(); path; path = queue.shift()) await store(cache, path).catch(() => null)
    }),
  )
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname
    if (/^\/api\/reports\/./.test(path) && !keep.has(path)) await cache.delete(req) // 只留最近 KEEP 份
  }
  syncedAt = Date.now()
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
