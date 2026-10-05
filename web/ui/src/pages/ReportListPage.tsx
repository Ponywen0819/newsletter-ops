import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { useDocumentTitle, useFetch } from '../hooks'
import { groupByMonth } from '../reportMonths'
import type { ReportSummary } from '../types'

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
  const months = groupByMonth(reports)
  return (
    <ReaderShell side={<h1>歷史晨報</h1>}>
      {months.length < 2 ? (
        // 只有一個月份（或沒有晨報）：分組沒有意義，維持一張列表
        <Rows items={reports} />
      ) : (
        months.map((month) => (
          // data-unit／class="unit"：右欄的錨點，捲動定位與 scroll-spy 都認這個（見 useSectionNav、styles.css）
          <section key={month.id} className="unit" data-unit={month.id}>
            <h2>{month.label}</h2>
            <Rows items={month.items} />
          </section>
        ))
      )}
    </ReaderShell>
  )
}
