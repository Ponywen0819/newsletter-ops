import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'

// 標題放左欄（位置對應晨報頁左欄的標題）、列表放右欄；載入中與出錯也在 ReaderShell 裡，外框不變
export function ReportListPage() {
  const { data, error } = useFetch(api.reports, [useRecovered()])
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
  return (
    <ReaderShell side={<h1>歷史晨報</h1>}>
      <ul className="reports">
        {data.length === 0 && <li>目前還沒有任何晨報。</li>}
        {data.map(({ date, headline }) => (
          <li key={date}>
            <Link to={`/reports/${date}`}>{date}</Link>
            <span>{headline}</span>
          </li>
        ))}
      </ul>
    </ReaderShell>
  )
}
