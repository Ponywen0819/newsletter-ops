import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, AuthExpiredError } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { ReportPanel } from '../components/ReportView'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'
import { useSession } from '../session'
import type { RunRecord } from '../types'

const RECHECK_MS = 60_000 // 晨報正在產生時多久再問一次（不常駐輪詢：只在這個狀態、頁面在前景時）
const STALE_MS = 10 * 60_000 // 回到前景時，上次載入超過這麼久就重問（隔夜開著的 App 才會換成今天的）

/** 網路瞬斷、連不上、閘道暫時錯誤：可以先沿用上一次的結果；伺服器確定的錯誤（4xx、500）與登入逾時要讓人看到 */
const transient = (e: Error) =>
  e instanceof ApiError ? e.status >= 502 && e.status <= 504 : !(e instanceof AuthExpiredError)

const STEP: Record<string, string> = { collect: '蒐集新聞', write: '撰寫報告', render: '排版', send: '寄信' }
const NO_RETRY = new Set([5, 6, 143]) // 和 worker 的 service.NO_RETRY 一致：認證、額度、被停止

/** 當天沒有晨報時，依執行紀錄說明原因；null ＝ 還沒跑或跑成功，照一般的「還沒產出」。 */
function runNote(run: RunRecord | null | undefined): { title: string; text: string } | null {
  if (!run) return null
  if (!run.finished_at) return { title: '晨報正在產生', text: '完成後這一頁會自己換成新的晨報。' }
  if (!run.exit_code) return null
  const step = run.step ? `在「${STEP[run.step] ?? run.step}」這步` : ''
  const next = noRetry(run) ? '不會自動重跑，修好後在主機上補跑。' : '30 分鐘後會自動重跑一次。'
  return { title: '晨報產生失敗', text: `${step}失敗（exit ${run.exit_code}），${next}` }
}

const noRetry = (run: RunRecord) => run.trigger === 'retry' || NO_RETRY.has(run.exit_code ?? 0)

/** 執行失敗、不會自動重跑時：本機直接給處理的入口（認證失敗去授權頁，其他去維運頁重跑），遠端只說要在主機上處理。 */
function FixHint({ run }: { run: RunRecord | null | undefined }) {
  const local = useSession()?.local
  if (!local || !run?.finished_at || !run.exit_code || !noRetry(run)) return null
  return (
    <>
      {' '}
      {run.exit_code === 5 ? <Link to="/auth">到授權頁處理</Link> : <Link to="/ops">到維運頁重跑</Link>}
    </>
  )
}

// 晨報以外的狀態沒有左欄內容，但一樣放在 ReaderShell：外框從載入中到載入完成都不變
export function TodayPage() {
  const [attempt, setAttempt] = useState(0) // 「再試一次」與產生中的定時重問
  const fetched = useFetch(api.today, [useRecovered(), attempt])
  // 重問期間、或重問遇到暫時性的錯誤（網路瞬斷）時照舊顯示上一次的結果：不閃「載入中」，也不把正在讀的舊晨報換成錯誤頁
  const [kept, setKept] = useState(fetched.data)
  if (fetched.data && fetched.data !== kept) setKept(fetched.data)
  const loadedAt = useRef(0)
  useEffect(() => {
    if (fetched.data) loadedAt.current = Date.now()
  }, [fetched.data])
  const error = fetched.error && (!kept || !transient(fetched.error)) ? fetched.error : undefined
  const data = error ? undefined : (fetched.data ?? kept)
  const shown = data?.report ? data.date : data?.fallback?.date
  useDocumentTitle(shown ? `每日晨間簡報 ${shown}` : '每日晨間簡報')

  // 晨報正在產生：每分鐘再問一次，好了就直接換上（裝成 App 時沒有重新整理鈕）；頁面在背景時不問，回到前景馬上問
  const generating = !!data && !data.report && !!data.run && !data.run.finished_at
  useEffect(() => {
    if (!generating) return
    const timer = window.setInterval(() => document.visibilityState === 'visible' && setAttempt((n) => n + 1), RECHECK_MS)
    return () => window.clearInterval(timer)
  }, [generating])
  // 回到前景：產生中馬上問；否則上次載入太久（例如昨晚開著的 App）也重問，才會換成今天的晨報
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      if (generating || (loadedAt.current && Date.now() - loadedAt.current > STALE_MS)) setAttempt((n) => n + 1)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
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
