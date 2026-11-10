import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { ReportPanel } from '../components/ReportView'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'
import type { RunRecord } from '../types'

const STEP: Record<string, string> = { collect: '蒐集新聞', write: '撰寫報告', render: '排版', send: '寄信' }
const NO_RETRY = new Set([5, 6, 143]) // 和 worker 的 service.NO_RETRY 一致：認證、額度、被停止

/** 當天沒有晨報時，依執行紀錄說明原因；null ＝ 還沒跑或跑成功，照一般的「還沒產出」。 */
function runNote(run: RunRecord | null | undefined): { title: string; text: string } | null {
  if (!run) return null
  if (!run.finished_at) return { title: '晨報正在產生', text: '完成後重新整理就看得到。' }
  if (!run.exit_code) return null
  const step = run.step ? `在「${STEP[run.step] ?? run.step}」這步` : ''
  const next =
    run.trigger === 'retry' || NO_RETRY.has(run.exit_code)
      ? '不會自動重跑，修好後在主機上補跑。'
      : '30 分鐘後會自動重跑一次。'
  return { title: '晨報產生失敗', text: `${step}失敗（exit ${run.exit_code}），${next}` }
}

// 晨報以外的狀態沒有左欄內容，但一樣放在 ReaderShell：外框從載入中到載入完成都不變
export function TodayPage() {
  const { data, error } = useFetch(api.today, [useRecovered()])
  const shown = data?.report ? data.date : data?.fallback?.date
  useDocumentTitle(shown ? `每日晨間簡報 ${shown}` : '每日晨間簡報')

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
    const note = runNote(data.run)
    const fb = data.fallback
    if (fb)
      return (
        <ReportPanel
          key={fb.date}
          report={fb.report}
          marks={fb.marks}
          flags={fb.flags}
          adjacent={{ prev: fb.prev }}
          banner={
            <p className="report-banner" role="status">
              <strong>{`${data.date} 的${note?.title ?? '晨報還沒產出'}`}</strong>
              {note ? `：${note.text}` : '。'}下面是最新一份（{fb.date}）。
            </p>
          }
        />
      )
    if (note)
      return (
        <ReaderShell>
          <Notice title={`${data.date} 的${note.title}`}>{note.text}</Notice>
        </ReaderShell>
      )
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
  return <ReportPanel key={data.date} report={data.report} marks={data.marks} flags={data.flags} adjacent={{ prev: data.prev }} />
}
