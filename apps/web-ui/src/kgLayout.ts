import type { KgEvent } from './types'

// 脈絡圖的版面：橫軸是日期（舊在左、新在右，一天一欄），同一天的事件由上往下排。
// 新聞的關係幾乎都是「後來的報導回頭指向先前的」，照時間排比力導向圖好讀，也不用另外的函式庫。

export const COL_W = 136
export const ROW_H = 52
export const RADIUS = 9
const PAD_X = 56
const PAD_TOP = 40
const PAD_BOTTOM = 24
const LABEL_CHARS = 9

export interface Placed {
  x: number
  y: number
}

export interface KgLayout {
  width: number
  height: number
  columns: { date: string; x: number }[]
  nodes: Map<number, Placed>
}

/** events 依日期分欄；同一欄照傳進來的順序排（後端給的是日期、id 由舊到新）。 */
export function layoutEvents(events: KgEvent[]): KgLayout {
  const dates = [...new Set(events.map((e) => e.date))].toSorted()
  const columns = dates.map((date, i) => ({ date, x: PAD_X + i * COL_W }))
  const colOf = new Map(columns.map((c) => [c.date, c]))
  const rows = new Map<string, number>()
  const nodes = new Map<number, Placed>()
  for (const event of events) {
    const row = rows.get(event.date) ?? 0
    rows.set(event.date, row + 1)
    nodes.set(event.id, { x: colOf.get(event.date)!.x, y: PAD_TOP + row * ROW_H + ROW_H / 2 })
  }
  const maxRows = Math.max(1, ...rows.values())
  return {
    width: PAD_X * 2 + Math.max(0, columns.length - 1) * COL_W,
    height: PAD_TOP + maxRows * ROW_H + PAD_BOTTOM,
    columns,
    nodes,
  }
}

/**
 * 從 from 畫到 to 的曲線，兩端在圓周上（箭頭不會蓋到圓）。
 * 不同欄：水平切線的 S 形；同一欄：往右鼓的弧，免得穿過中間的節點。
 */
export function edgePath(from: Placed, to: Placed): string {
  if (from.x === to.x) {
    const bulge = Math.min(COL_W / 2 - RADIUS, 24 + Math.abs(to.y - from.y) / 4)
    const [x1, x2] = [from.x + RADIUS, to.x + RADIUS]
    return `M${x1},${from.y} C${x1 + bulge},${from.y} ${x2 + bulge},${to.y} ${x2},${to.y}`
  }
  const dir = Math.sign(to.x - from.x)
  const [x1, x2] = [from.x + dir * RADIUS, to.x - dir * RADIUS]
  const mid = (x1 + x2) / 2
  return `M${x1},${from.y} C${mid},${from.y} ${mid},${to.y} ${x2},${to.y}`
}

/** 節點下方的短標籤；完整的摘要在詳情與列表裡。 */
export function shortLabel(text: string): string {
  const chars = [...text]
  return chars.length > LABEL_CHARS ? `${chars.slice(0, LABEL_CHARS - 1).join('')}…` : text
}

/** MM/DD：欄的標題 */
export const shortDate = (date: string) => date.slice(5).replace('-', '/')
