import { api, ApiError } from './api'
import { API_CACHE } from './cacheName'
import { check } from './connectivity'
import type { Mark } from './types'

/**
 * 離線時的「有用／沒用」：先排隊（localStorage），恢復連線後補送。
 * 衝突不改後端：補送前先讀伺服器現在的標記，和按下時畫面上的（before）不同＝離線期間在別處改過，以伺服器為準。
 */
const KEY = 'feedback-queue'
const MAX_TRIES = 5

interface Pending {
  mark: Mark
  /** 第一次按下時畫面上原本的標記 */
  before: Mark
  at: number
  tries: number
}
export type Queue = Record<string, Pending>

let queue: Queue = load()
let notice = ''
const listeners = new Set<() => void>()

function load(): Queue {
  try {
    const q: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    return q && typeof q === 'object' ? (q as Queue) : {}
  } catch {
    return {}
  }
}

function commit(next: Queue): void {
  queue = next
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // 存不了就只活在這次開著的期間
  }
  listeners.forEach((l) => l())
}

function say(text: string): void {
  notice = text
  listeners.forEach((l) => l())
}

export const getQueue = () => queue
export const getNotice = () => notice
export const clearNotice = () => say('')

export function subscribeQueue(notify: () => void): () => void {
  listeners.add(notify)
  return () => listeners.delete(notify)
}

/** 排一筆。同一個 uid 只留最後一次；改回按下前的標記就整筆拿掉（不用送）。 */
export function enqueue(uid: string, mark: Mark, before: Mark): void {
  const first = queue[uid]?.before ?? before
  const { [uid]: _old, ...rest } = queue
  commit(mark === first ? rest : { ...rest, [uid]: { mark, before: first, at: Date.now(), tries: 0 } })
}

/** 改寫本機副本（today、單日晨報）裡這則的標記，離線重開也看得到。保留原本的 headers（含 ETag）。 */
export async function patchCopies(uid: string, mark: Mark): Promise<void> {
  if (typeof caches === 'undefined') return
  try {
    const cache = await caches.open(API_CACHE)
    for (const req of await cache.keys()) {
      if (!/^\/api\/(today|reports\/.+)$/.test(new URL(req.url).pathname)) continue
      const res = await cache.match(req)
      const text = res ? await res.text() : ''
      if (!res || !text.includes(uid)) continue
      const payload = JSON.parse(text) as { marks?: Record<string, Mark> }
      const marks = { ...payload.marks }
      if (mark) marks[uid] = mark
      else delete marks[uid]
      await cache.put(req, new Response(JSON.stringify({ ...payload, marks }), { headers: res.headers }))
    }
  } catch {
    // 改不了副本只影響離線重開時的顯示
  }
}

const fatal = (e: unknown) => e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 401 && e.status !== 403

/** 補送整個佇列。連不上或登入逾時就整個保留；回傳這次送出的筆數。 */
export async function flush(): Promise<number> {
  if (!Object.keys(queue).length || (await check(true)) !== 'ok') return 0
  let sent = 0
  let conflicts = 0
  let dropped = 0
  for (const [uid, item] of Object.entries(queue)) {
    try {
      const server = await api.feedbackTarget(uid)
      if (typeof server?.mark !== 'string') throw new Error('回應不是標記（可能被導到登入頁）')
      if (server.mark === item.before) {
        await api.feedback(uid, item.mark)
        sent++
      } else conflicts++
    } catch (e) {
      if (!fatal(e) && item.tries + 1 < MAX_TRIES) {
        if ((await check(true)) !== 'ok') break // 途中斷線或登入逾時：剩下的原封不動
        commit({ ...queue, [uid]: { ...item, tries: item.tries + 1 } })
        continue
      }
      dropped++
    }
    const { [uid]: _done, ...rest } = queue
    commit(rest)
  }
  const parts = [
    conflicts && `${conflicts} 則標記在其他地方已經改過，以那邊為準`,
    dropped && `${dropped} 則標記補送失敗，已放棄`,
  ].filter(Boolean)
  if (parts.length) say(parts.join('；'))
  return sent
}

/** 測試用 */
export function resetQueue(): void {
  queue = load()
  notice = ''
}
