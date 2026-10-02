import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { useDocumentTitle, useFetch } from '../hooks'

export function ReportListPage() {
  const { data, error } = useFetch(api.reports, [])
  useDocumentTitle('歷史晨報')

  if (error) return <Notice title="讀取失敗">{error.message}</Notice>
  if (!data) return <Loading />
  return (
    <>
      <h1>歷史晨報</h1>
      <ul className="reports">
        {data.length === 0 && <li>目前還沒有任何晨報。</li>}
        {data.map(({ date, headline }) => (
          <li key={date}>
            <Link to={`/reports/${date}`}>{date}</Link>
            <span>{headline}</span>
          </li>
        ))}
      </ul>
    </>
  )
}
