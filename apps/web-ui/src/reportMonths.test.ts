import { describe, expect, it } from 'vitest'
import { groupByMonth } from './reportMonths'

const r = (date: string) => ({ date, headline: `h ${date}` })

describe('groupByMonth', () => {
  it('依 YYYY-MM 分組，組內與組間都保持輸入順序（後端已新到舊）', () => {
    const groups = groupByMonth([r('2026-10-05'), r('2026-10-02'), r('2026-09-30'), r('2026-09-28')])
    expect(groups.map((g) => [g.id, g.label, g.items.map((i) => i.date)])).toEqual([
      ['m-2026-10', '2026-10', ['2026-10-05', '2026-10-02']],
      ['m-2026-09', '2026-09', ['2026-09-30', '2026-09-28']],
    ])
  })

  it('同月份只有一組；跨年的同月份（2026-01 與 2025-01）是兩組', () => {
    expect(groupByMonth([r('2026-10-05'), r('2026-10-04')]).map((g) => g.id)).toEqual(['m-2026-10'])
    expect(groupByMonth([r('2026-01-03'), r('2025-01-03')]).map((g) => g.id)).toEqual(['m-2026-01', 'm-2025-01'])
  })

  it('輸入沒排好序也不會產生重複的 id（id 拿來當錨點與網址 hash）', () => {
    const groups = groupByMonth([r('2026-10-05'), r('2026-09-30'), r('2026-10-01')])
    expect(groups.map((g) => g.id)).toEqual(['m-2026-10', 'm-2026-09'])
    expect(groups[0]!.items.map((i) => i.date)).toEqual(['2026-10-05', '2026-10-01'])
  })

  it('沒有晨報：空陣列', () => {
    expect(groupByMonth([])).toEqual([])
  })
})
