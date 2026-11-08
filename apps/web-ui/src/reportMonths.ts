import type { ReportSummary } from './types'

export interface MonthGroup {
  /** `m-YYYY-MM`：右欄區塊的錨點（data-unit）與網址 hash */
  id: string
  /** `YYYY-MM`：與列表上的日期同一種寫法 */
  label: string
  items: ReportSummary[]
}

/** 依 date 的 YYYY-MM 分組。順序照輸入（後端已經新到舊），沒排好序也不會產生重複的組。 */
export function groupByMonth(reports: ReportSummary[]): MonthGroup[] {
  const groups = new Map<string, MonthGroup>()
  for (const report of reports) {
    const label = report.date.slice(0, 7)
    let group = groups.get(label)
    if (!group) groups.set(label, (group = { id: `m-${label}`, label, items: [] }))
    group.items.push(report)
  }
  return [...groups.values()]
}
