import { API_CACHE } from './cacheName'
import type { ReportSummary } from './types'

/**
 * 離線副本的核心：把今日、歷史列表與最近 KEEP 份晨報存進 API_CACHE。頁面（sync.ts）與 service worker（sw.ts 的
 * periodicsync）共用，所以這個檔不能碰 window／document／localStorage，也不能 import 會碰它們的模組。
 */
export const KEEP = 30
const PARALLEL = 4
/** 最近一次同步的紀錄也放進 API_CACHE：service worker 沒有 localStorage，頁面啟動時再讀回去。 */
export const RECORD_URL = '/__offline-sync'
export const PERIODIC_TAG = 'offline-copy'

/** 最近一次同步：at 是時間（ms），count 是副本裡的晨報份數，failed 是這次沒更新到（留著舊版或沒有）的份數。 */
export interface SyncRecord {
  at: number
  count: number
  failed: number
}

export function isRecord(rec: unknown): rec is SyncRecord {
  const r = rec as Partial<SyncRecord> | null
  return !!r && [r.at, r.count, r.failed].every((v) => typeof v === 'number' && Number.isFinite(v))
}

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

/**
 * 同步一次。false＝沒做（列表失敗、離線、登入逾時：舊副本原封不動）；null＝做了但一份都沒寫入（不算同步過）；
 * 否則回傳這次的紀錄（也寫進 RECORD_URL）。「同步過」＝列表成功、而且至少寫入一份（一份都沒有晨報時也算）。
 */
export async function refresh(): Promise<SyncRecord | null | false> {
  if (typeof caches === 'undefined') return false
  const cache = await caches.open(API_CACHE)
  const list = await store(cache, '/api/reports').catch(() => null)
  if (!list) return false
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
  const missed = [...keep].filter((p) => failed.has(p))
  if (keep.size > 0 && missed.length === keep.size) return null
  const cached = new Set((await cache.keys()).map((req) => new URL(req.url).pathname))
  const rec = { at: Date.now(), count: [...keep].filter((p) => cached.has(p)).length, failed: missed.length }
  await cache.put(RECORD_URL, new Response(JSON.stringify(rec), { headers: { 'Content-Type': 'application/json' } }))
  return rec
}

/** RECORD_URL 裡的紀錄（service worker 背景同步寫的也在這）；沒有或壞掉回 null。 */
export async function cachedRecord(): Promise<SyncRecord | null> {
  if (typeof caches === 'undefined') return null
  try {
    const hit = await (await caches.open(API_CACHE)).match(RECORD_URL)
    const rec: unknown = hit ? await hit.json() : null
    return isRecord(rec) ? rec : null
  } catch {
    return null
  }
}
