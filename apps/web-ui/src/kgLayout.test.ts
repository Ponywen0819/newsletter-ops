import { describe, expect, it } from 'vitest'
import { COL_W, edgePath, layoutEvents, RADIUS, ROW_H, shortDate, shortLabel } from './kgLayout'
import type { KgEvent } from './types'

const ev = (id: number, date: string): KgEvent => ({
  id,
  uid: String(id).padStart(16, '0'),
  date,
  summary: `事件 ${id}`,
  quote: '',
  title: '',
  url: '',
  source: 'S',
  status: 'active',
  sources: 1,
  report: null,
  entities: [],
})

describe('layoutEvents', () => {
  it('一天一欄，舊在左；同一天由上往下排', () => {
    const { columns, nodes, width, height } = layoutEvents([ev(1, '2026-09-10'), ev(2, '2026-09-20'), ev(3, '2026-09-20'), ev(4, '2026-09-28')])
    expect(columns.map((c) => c.date)).toEqual(['2026-09-10', '2026-09-20', '2026-09-28'])
    expect(nodes.get(2)!.x - nodes.get(1)!.x).toBe(COL_W)
    expect(nodes.get(3)!.x).toBe(nodes.get(2)!.x)
    expect(nodes.get(3)!.y - nodes.get(2)!.y).toBe(ROW_H)
    expect(nodes.get(4)!.y).toBe(nodes.get(1)!.y)
    expect(width).toBeGreaterThan(nodes.get(4)!.x)
    expect(height).toBeGreaterThan(nodes.get(3)!.y + RADIUS)
  })

  it('沒有事件也不會出錯', () => {
    const { columns, nodes, width, height } = layoutEvents([])
    expect(columns).toEqual([])
    expect(nodes.size).toBe(0)
    expect(width).toBeGreaterThan(0)
    expect(height).toBeGreaterThan(0)
  })
})

describe('edgePath', () => {
  it('不同欄：兩端停在圓周上', () => {
    expect(edgePath({ x: 200, y: 50 }, { x: 64, y: 100 })).toBe(`M${200 - RADIUS},50 C132,50 132,100 ${64 + RADIUS},100`)
  })

  it('同一欄：往右鼓，不穿過中間的節點', () => {
    const d = edgePath({ x: 100, y: 50 }, { x: 100, y: 150 })
    const xs = [...d.matchAll(/(-?[\d.]+),/g)].map((m) => Number(m[1]))
    expect(Math.min(...xs)).toBe(100 + RADIUS)
  })
})

it('shortLabel／shortDate', () => {
  expect(shortLabel('短')).toBe('短')
  expect(shortLabel('一二三四五六七八九十')).toBe('一二三四五六七八…')
  const face = String.fromCodePoint(0x1f600)
  expect(shortLabel(face.repeat(12))).toBe(`${face.repeat(8)}…`) // 不會把 emoji 切成兩半
  expect(shortDate('2026-09-28')).toBe('09/28')
})
