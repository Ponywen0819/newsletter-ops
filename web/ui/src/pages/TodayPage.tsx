import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { ReportPanel } from '../components/ReportView'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'

// 晨報以外的狀態沒有左欄內容，但一樣放在 ReaderShell：外框從載入中到載入完成都不變
export function TodayPage() {
  const { data, error } = useFetch(api.today, [useRecovered()])
  useDocumentTitle(data?.report ? `每日晨間簡報 ${data.date}` : '每日晨間簡報')

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
  if (!data.report) {
    return (
      <ReaderShell>
        <Notice title={`${data.date} 的晨報還沒產出`}>
          {data.latest ? (
            <>
              最新一份是 <Link to={`/reports/${data.latest}`}>{data.latest}</Link>。
            </>
          ) : (
            '目前還沒有任何晨報。'
          )}
        </Notice>
      </ReaderShell>
    )
  }
  return <ReportPanel key={data.date} report={data.report} marks={data.marks} />
}
