import { useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReportPanel } from '../components/ReportView'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'
import { NotFoundPage } from './NotFoundPage'

export function ReportPage() {
  const { date = '' } = useParams()
  const { data, error } = useFetch(() => api.report(date), [date, useRecovered()])
  useDocumentTitle(data ? `每日晨間簡報 ${data.date}` : '每日晨間簡報')

  if (error instanceof ApiError && error.status === 404) return <NotFoundPage />
  if (error) return <Notice title="讀取失敗">{error.message}</Notice>
  if (!data) return <Loading />
  return <ReportPanel key={data.date} report={data.report} marks={data.marks} />
}
