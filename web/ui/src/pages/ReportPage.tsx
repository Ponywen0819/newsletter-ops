import { useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { ReportPanel } from '../components/ReportView'
import { useDocumentTitle, useFetch } from '../hooks'
import { NotFoundPage } from './NotFoundPage'

// 晨報以外的狀態沒有左欄內容，但一樣放在 ReaderShell：外框從載入中到載入完成都不變
export function ReportPage() {
  const { date = '' } = useParams()
  const { data, error } = useFetch(() => api.report(date), [date])
  useDocumentTitle(data ? `每日晨間簡報 ${data.date}` : '每日晨間簡報')

  if (error instanceof ApiError && error.status === 404)
    return (
      <ReaderShell>
        <NotFoundPage />
      </ReaderShell>
    )
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
  return <ReportPanel key={data.date} report={data.report} marks={data.marks} />
}
