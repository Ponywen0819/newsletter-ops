import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { ReportPanel } from '../components/ReportView'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'
import { useSession } from '../session'
import type { RunRecord } from '../types'

const RECHECK_MS = 60_000 // 晨報正在產生時多久再問一次（不常駐輪詢：只在這個狀態、頁面在前景時）

const STEP: Record<string, string> = { collect: '蒐集新聞', write: '撰寫報告', render: '排版', send: '寄信' }
const NO_RETRY = new Set([5, 6, 143]) // 和 worker 的 service.NO_RETRY 一致：認證、額度、被停止

/** 當天沒有晨報時，依執行紀錄說明原因；null ＝ 還沒跑或跑成功，照一般的「還沒產出」。 */
function runNote(run: RunRecord | null | undefined): { title: string; text: string } | null {
  if (!run) return null
  if (!run.finished_at) return { title: '晨報正在產生', text: '完成後這一頁會自己換成新的晨報。' }
  if (!run.exit_code) return null
  const step = run.step ? `在「${STEP[run.step] ?? run.step}」這步` : ''
  const next =
    run.trigger === 'retry' || NO_RETRY.has(run.exit_code)
      ? '不會自動重跑，修好後在主機上補跑。'
      : '30 分鐘後會自動重跑一次。'
  return { title: '晨報產生失敗', text: `${step}失敗（exit ${run.exit_code}），${next}` }
}

/** 執行失敗、不會自動重跑時：本機（能開維運頁）直接給重跑的入口，遠端只說要在主機上處理。 */
function FixHint({ run }: { run: RunRecord | null | undefined }) {
  const local = useSession()?.local
  if (!local || !run?.finished_at || !run.exit_code) return null
  return (
    <>
      {' '}
      <Link to="/ops">到維運頁重跑</Link>
    </>
  )
}

// 晨報以外的狀態沒有左欄內容，但一樣放在 ReaderShell：外框從載入中到載入完成都不變
export function TodayPage() {
  const [attempt, setAttempt] = useState(0) // 「再試一次」與產生中的定時重問
  const fetched = useFetch(api.today, [useRecovered(), attempt])
  // 重問期間照舊顯示上一次的結果，不要每分鐘閃一下「載入中」
  const [kept, setKept] = useState(fetched.data)
  if (fetched.data && fetched.data !== kept) setKept(fetched.data)
  const data = fetched.data ?? (fetched.loading ? kept : undefined)
  const error = fetched.error
  const shown = data?.report ? data.date : data?.fallback?.date
  useDocumentTitle(shown ? `每日晨間簡報 ${shown}` : '每日晨間簡報')

  // 晨報正在產生：每分鐘再問一次，好了就直接換上（裝成 App 時沒有重新整理鈕）；頁面在背景時不問
  const generating = !!data && !data.report && !!data.run && !data.run.finished_at
  useEffect(() => {
    if (!generating) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') setAttempt((n) => n + 1)
    }, RECHECK_MS)
    return () => window.clearInterval(timer)
  }, [generating])

  if (error)
    return (
      <ReaderShell>
        <Notice title="讀取失敗">{error.message}</Notice>
        <p>
          <button type="button" className="auth-btn auth-primary" onClick={() => setAttempt((n) => n + 1)}>
            再試一次
          </button>
        </p>
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
              <FixHint run={data.run} />
            </p>
          }
        />
      )
    if (note)
      return (
        <ReaderShell>
          <Notice title={`${data.date} 的${note.title}`}>
            {note.text}
            <FixHint run={data.run} />
          </Notice>
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
