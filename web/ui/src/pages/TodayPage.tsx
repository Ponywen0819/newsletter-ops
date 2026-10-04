import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReportPanel } from '../components/ReportView'
import { useDocumentTitle, useFetch } from '../hooks'

export function TodayPage() {
  const { data, error } = useFetch(api.today, [])
  useDocumentTitle(data?.report ? `每日晨間簡報 ${data.date}` : '每日晨間簡報')

  if (error) return <Notice title="讀取失敗">{error.message}</Notice>
  if (!data) return <Loading />
  if (!data.report) {
    return (
      <Notice title={`${data.date} 的晨報還沒產出`}>
        {data.latest ? (
          <>
            最新一份是 <Link to={`/reports/${data.latest}`}>{data.latest}</Link>。
          </>
        ) : (
          '目前還沒有任何晨報。'
        )}
      </Notice>
    )
  }
  return <ReportPanel key={data.date} report={data.report} marks={data.marks} />
}
