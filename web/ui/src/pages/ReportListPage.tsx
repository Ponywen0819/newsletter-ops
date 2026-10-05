import { useMemo, useRef } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { useDocumentTitle, useFetch } from '../hooks'
import { groupByMonth, type MonthGroup } from '../reportMonths'
import type { ReportSummary } from '../types'
import { useSectionNav } from '../useSectionNav'

function Rows({ items }: { items: ReportSummary[] }) {
  return (
    <ul className="reports">
      {items.length === 0 && <li>目前還沒有任何晨報。</li>}
      {items.map(({ date, headline }) => (
        <li key={date}>
          <Link to={`/reports/${date}`}>{date}</Link>
          <span>{headline}</span>
        </li>
      ))}
    </ul>
  )
}

// 標題放左欄（位置對應晨報頁左欄的標題）、列表放右欄；載入中與出錯也在 ReaderShell 裡，外框不變
export function ReportListPage() {
  const { data, error } = useFetch(api.reports, [])
  useDocumentTitle('歷史晨報')

  if (error)
    return (
      <ReaderShell>
        <Notice title="讀取失敗">{error.message}</Notice>
      </ReaderShell>
    )
  if (!data)
    return (
      <ReaderShell>
        <Loading />
      </ReaderShell>
    )
  return <ReportList reports={data} />
}

function ReportList({ reports }: { reports: ReportSummary[] }) {
  const months = useMemo(() => groupByMonth(reports), [reports])
  // 只有一個月份（或沒有晨報）：分組與目錄都沒有意義，維持一張列表
  if (months.length < 2)
    return (
      <ReaderShell side={<h1>歷史晨報</h1>}>
        <Rows items={reports} />
      </ReaderShell>
    )
  return <MonthList months={months} />
}

/** 兩個月以上：右欄依月份分組；≥1100px 左欄多一份月份目錄（連動邏輯與晨報頁的目錄共用，見 useSectionNav）。 */
function MonthList({ months }: { months: MonthGroup[] }) {
  const ids = useMemo(() => months.map((m) => m.id), [months])
  const rootRef = useRef<HTMLDivElement>(null)
  const navRef = useRef<HTMLElement>(null)
  const { activeId, onLinkClick } = useSectionNav(ids, rootRef, navRef)
  return (
    <ReaderShell
      ref={rootRef}
      side={
        <>
          <h1>歷史晨報</h1>
          <nav ref={navRef} className="reader-nav reader-nav-months" aria-label="月份目錄">
            <ul>
              {months.map((month) => (
                <li key={month.id}>
                  <a href={`#${month.id}`} aria-current={month.id === activeId ? 'true' : undefined} onClick={onLinkClick(month.id)}>
                    <span>{month.label}</span> <small className="reader-nav-count">{month.items.length} 份</small>
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </>
      }
    >
      {months.map((month) => (
        // data-unit／class="unit"：右欄的錨點，捲動定位與 scroll-spy 都認這個（見 useSectionNav、styles.css）
        <section key={month.id} className="unit" data-unit={month.id}>
          <h2>{month.label}</h2>
          <Rows items={month.items} />
        </section>
      ))}
    </ReaderShell>
  )
}
