import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { api } from '../api'
import { EXTERNAL } from '../components/Inline'
import { Loading } from '../components/Notice'
import { useDocumentTitle, useFetch } from '../hooks'
import { useSession } from '../session'
import type { MetricsRun, OpsJob, OpsKind, OpsStatus, RunStep, SourceFile } from '../types'
import { NotFoundPage } from './NotFoundPage'

const STEPS: RunStep[] = ['collect', 'write', 'render', 'send']
const STEP_LABEL: Record<RunStep, string> = { collect: '蒐集新聞', write: '撰寫報告', render: '排版', send: '寄信' }
const KIND_LABEL: Record<OpsKind, string> = {
  run: '每日流程',
  'kg-rebuild': '重建知識圖譜',
  'sources-check': '試抓來源',
  rewrite: '重寫報告（devtools）',
}
const TRIGGER_LABEL: Record<string, string> = {
  schedule: '排程',
  catchup: '補跑',
  retry: '自動重跑',
  manual: '手動',
  cli: '指令列',
}
// 與 apps/worker 的 EXIT_HINT 同義
const EXIT_HINT: Record<number, string> = {
  2: '環境問題（例如沒有 OAuth token）',
  5: 'Claude 授權失效，到「Claude 授權」重新貼上 token',
  6: '訂閱額度用完，等額度恢復',
  75: '已有另一條在跑，這次沒有執行',
  124: '超過時限被停掉',
  143: '被停止（worker 重啟或關機）',
}
const SAFE_HREF = /^https?:\/\//i

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString('zh-TW', { hour12: false }) : '—')
const stepLabel = (step: string | null | undefined) => (step && step in STEP_LABEL ? STEP_LABEL[step as RunStep] : step || '?')
/** 重跑從失敗的那步開始；寄信需要排版的輸出，所以從排版（同 worker 的 resume_from） */
const resumeFrom = (step: string | null | undefined): RunStep =>
  step === 'send' ? 'render' : STEPS.includes(step as RunStep) ? (step as RunStep) : 'collect'

function exitText(code: number | null | undefined, step?: string | null): string {
  if (code === null || code === undefined) return '還在跑'
  if (code === 0) return '成功'
  const where = step ? `在「${stepLabel(step)}」` : ''
  const hint = EXIT_HINT[code]
  return `${where}失敗（exit ${code}）${hint ? `：${hint}` : ''}`
}

function OpsNav() {
  return (
    <nav className="ops-nav" aria-label="維運">
      <NavLink to="/ops" end>
        執行
      </NavLink>
      <NavLink to="/ops/metrics">執行紀錄</NavLink>
      <NavLink to="/ops/config">設定</NavLink>
    </nav>
  )
}

/** /ops* 只服務本機：nginx 在 Tunnel 那一側回 404，這裡也不畫出來（同 /auth）。 */
function LocalOnly({ title, children }: { title: string; children: ReactNode }) {
  const session = useSession()
  useDocumentTitle(title)
  if (!session) return <Loading />
  if (!session.local) return <NotFoundPage />
  return (
    <>
      <h1>{title}</h1>
      <OpsNav />
      {children}
    </>
  )
}

/* ---------- /ops：狀態與手動執行 ---------- */

const POLL_BUSY = 3000
const POLL_IDLE = 15000

const isBusy = (s: OpsStatus) => s.worker.state === 'running'

/** 有東西在跑時每 3 秒問一次，否則 15 秒。refresh：馬上再問一次（送出要求之後）。 */
function useOpsStatus() {
  const [status, setStatus] = useState<OpsStatus>()
  const [error, setError] = useState<Error>()
  const load = useRef<() => void>(() => {})
  useEffect(() => {
    let live = true
    let timer = 0
    const next = (busy: boolean) => {
      window.clearTimeout(timer)
      timer = window.setTimeout(run, busy ? POLL_BUSY : POLL_IDLE)
    }
    function run() {
      window.clearTimeout(timer)
      api.ops.status().then(
        (s) => {
          if (!live) return
          setStatus(s)
          setError(undefined)
          next(isBusy(s))
        },
        (e: unknown) => {
          if (!live) return
          setError(e instanceof Error ? e : new Error(String(e)))
          next(false)
        },
      )
    }
    load.current = run
    run()
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [])
  const refresh = useCallback(() => load.current(), [])
  return { status, error, refresh, busy: !!status && isBusy(status) }
}

function WorkerView({ status }: { status: OpsStatus }) {
  const { worker } = status
  const current = worker.current
  return (
    <dl className="ops-dl">
      <dt>worker</dt>
      <dd className={worker.online ? 'ops-ok' : 'ops-bad'}>
        {worker.online ? '運作中' : '連不上'}
        {!worker.online && <span className="ops-hint">（worker 沒在跑或網路不通，手動執行送不出去；下面是上次留下的紀錄）</span>}
      </dd>
      {worker.online && (
        <>
          <dt>每日執行</dt>
          <dd>{worker.scheduled ? `${worker.run_at}（下次 ${when(worker.next_run_at)}）` : '排程關閉（測試區），只接受手動執行'}</dd>
          <dt>目前</dt>
          <dd>
            {current
              ? `執行中：${KIND_LABEL[current.kind] ?? current.kind}（${TRIGGER_LABEL[current.trigger] ?? current.trigger}，${when(current.started_at)} 開始）`
              : '閒置'}
          </dd>
        </>
      )}
    </dl>
  )
}

function LastRun({ status }: { status: OpsStatus }) {
  const last = status.last_run
  if (!last) return <p className="ops-note">還沒有執行紀錄。</p>
  const failed = !!last.exit_code
  return (
    <dl className="ops-dl">
      <dt>日期</dt>
      <dd>
        {last.date}（{TRIGGER_LABEL[last.trigger] ?? last.trigger}）
      </dd>
      <dt>時間</dt>
      <dd>
        {when(last.started_at)} → {last.finished_at ? when(last.finished_at) : '還在跑'}
      </dd>
      <dt>結果</dt>
      <dd className={last.finished_at ? (failed ? 'ops-bad' : 'ops-ok') : undefined}>
        {last.finished_at ? exitText(last.exit_code, last.step) : '還在跑'}
      </dd>
    </dl>
  )
}

function jobState(job: OpsJob): { text: string; tone?: 'ok' | 'bad' } {
  if (!job.finished_at) return { text: '執行中…' }
  return job.exit_code === 0 ? { text: '完成', tone: 'ok' } : { text: exitText(job.exit_code), tone: 'bad' }
}

function JobView({ status }: { status: OpsStatus }) {
  const { job, log } = status
  if (!job) return <p className="ops-note">還沒有手動執行過。</p>
  const state = jobState(job)
  return (
    <>
      <dl className="ops-dl">
        <dt>工作</dt>
        <dd>
          {KIND_LABEL[job.kind] ?? job.kind}
          {job.kind === 'run' && job.from && `（從「${STEP_LABEL[job.from]}」開始）`}
        </dd>
        <dt>時間</dt>
        <dd>
          {when(job.started_at ?? job.requested_at)}
          {job.finished_at && ` → ${when(job.finished_at)}`}
        </dd>
        <dt>狀態</dt>
        <dd className={state.tone ? `ops-${state.tone}` : undefined}>{state.text}</dd>
      </dl>
      {log.length > 0 && (
        <pre className="ops-log" aria-label="執行輸出（最後幾行）">
          {log.join('\n')}
        </pre>
      )}
    </>
  )
}

type Message = { kind: 'ok' | 'err'; text: string }

function OpsPanel() {
  const { status, error, refresh, busy } = useOpsStatus()
  const [chosen, setChosen] = useState<RunStep>()
  const [sending, setSending] = useState(false)
  const [message, setMessage] = useState<Message>()
  if (!status) return error ? <p className="ops-msg ops-err">{error.message}</p> : <Loading />

  const last = status.last_run
  const suggested = last && last.exit_code ? resumeFrom(last.step) : 'collect'
  const from = chosen ?? suggested
  const blocked = sending || busy || !status.worker.online

  async function send(kind: OpsKind, confirmText: string, start?: RunStep) {
    if (!window.confirm(confirmText)) return
    setSending(true)
    setMessage(undefined)
    try {
      await api.ops.request(kind, start)
      setMessage({ kind: 'ok', text: '已開始' })
    } catch (err) {
      setMessage({ kind: 'err', text: (err instanceof Error && err.message) || '送出失敗，請重試' })
    } finally {
      setSending(false)
      refresh()
    }
  }

  const runConfirm =
    from === 'collect' || from === 'write'
      ? `立刻跑每日流程（從「${STEP_LABEL[from]}」開始）？會用到 Claude 訂閱額度，完成後寄出 email。`
      : `立刻從「${STEP_LABEL[from]}」開始，用今天已經寫好的報告重新排版並寄出 email？`

  return (
    <>
      <section className="ops-section" aria-label="排程">
        <h2>排程</h2>
        <WorkerView status={status} />
      </section>

      <section className="ops-section" aria-label="上次每日流程">
        <h2>上次每日流程</h2>
        <LastRun status={status} />
      </section>

      <section className="ops-section" aria-label="手動執行">
        <h2>手動執行</h2>
        <div className="ops-row">
          <label htmlFor="ops-from">從哪一步開始</label>
          <select id="ops-from" value={from} onChange={(ev) => setChosen(ev.target.value as RunStep)}>
            {STEPS.map((s) => (
              <option key={s} value={s}>
                {STEP_LABEL[s]}
                {s === suggested && last?.exit_code ? '（上次失敗的那步）' : ''}
              </option>
            ))}
          </select>
          <button type="button" className="ops-btn ops-primary" disabled={blocked} onClick={() => void send('run', runConfirm, from)}>
            執行每日流程
          </button>
        </div>
        <p className="ops-note">
          從中間的步驟開始會沿用今天已有的產出（例如寄信設定修好後從「排版」重寄，不必重寫報告）。選「寄信」會從排版開始，因為寄信需要排版的輸出。
        </p>
        <ul className="ops-jobs">
          <li>
            <button
              type="button"
              className="ops-btn"
              disabled={blocked}
              onClick={() => void send('sources-check', '抓一次所有來源，看哪些來源壞了？（不寫任何檔案，也不用 Claude）')}
            >
              試抓來源
            </button>
            <span>抓一次所有來源，列出抓到幾則與失敗的來源。不寫檔、不用 Claude，約幾分鐘。</span>
          </li>
          <li>
            <button
              type="button"
              className="ops-btn ops-danger"
              disabled={blocked}
              onClick={() =>
                void send(
                  'kg-rebuild',
                  '清掉知識圖譜，依日期重跑全部的新聞資料？\n\n會用掉不少 Claude 訂閱額度、可能要跑很久，期間每日流程不會開始。你標的「事實有誤／確認可信」會保留。',
                )
              }
            >
              重建知識圖譜
            </button>
            <span>清掉知識圖譜，依日期重跑全部的新聞資料。萃取規則改了、或資料庫壞掉時用。很花額度與時間。</span>
          </li>
        </ul>
        <p className={`ops-msg${message ? ` ops-${message.kind}` : ''}`} role="status">
          {message?.text ?? (error ? `更新狀態失敗：${error.message}` : '')}
        </p>
      </section>

      <section className="ops-section" aria-label="最近一次手動執行">
        <h2>最近一次手動執行</h2>
        <JobView status={status} />
      </section>
    </>
  )
}

export function OpsPage() {
  return (
    <LocalOnly title="維運">
      <OpsPanel />
    </LocalOnly>
  )
}

/* ---------- /ops/metrics：執行紀錄 ---------- */

const DAYS = [7, 14, 30, 90] as const
const STAGE_COLS: [string, string][] = [
  ['fetch', '抓取'],
  ['curate', '整理'],
  ['search', '搜尋'],
  ['enrich', '補原文'],
  ['claude', '撰寫'],
  ['render', '排版'],
]

function secs(s: number | undefined): string {
  if (s === undefined) return '—'
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  return `${m}m${String(Math.round(s - m * 60)).padStart(2, '0')}s`
}

const num = (n: number | null | undefined) => (n === null || n === undefined ? '—' : n.toLocaleString('en-US'))

function resultText(run: MetricsRun): { text: string; tone?: 'ok' | 'bad' } {
  if (!run.result) return { text: '—' }
  if (run.result.exit_code === 0) return { text: '成功', tone: 'ok' }
  return { text: `${stepLabel(run.result.step)} exit ${run.result.exit_code}`, tone: 'bad' }
}

function MetricsTable({ runs, showLabel }: { runs: MetricsRun[]; showLabel: boolean }) {
  return (
    <div className="ops-table-wrap">
      <table className="ops-table">
        <thead>
          <tr>
            <th scope="col">日期</th>
            <th scope="col">開始</th>
            {showLabel && <th scope="col">標籤</th>}
            <th scope="col">結果</th>
            <th scope="col">總耗時</th>
            {STAGE_COLS.map(([, label]) => (
              <th scope="col" key={label}>
                {label}
              </th>
            ))}
            <th scope="col">則數</th>
            <th scope="col">輸入 token</th>
            <th scope="col">輸出 token</th>
            <th scope="col">回合</th>
            <th scope="col">費用（USD）</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, i) => {
            const result = resultText(run)
            return (
              <tr key={`${run.date}-${run.time}-${i}`}>
                <th scope="row">{run.date}</th>
                <td>
                  {run.time}
                  {run.trigger && run.trigger !== 'schedule' && <span className="ops-hint"> {TRIGGER_LABEL[run.trigger] ?? run.trigger}</span>}
                </td>
                {showLabel && <td>{run.label}</td>}
                <td className={result.tone ? `ops-${result.tone}` : undefined}>{result.text}</td>
                <td>{secs(run.seconds.pipeline)}</td>
                {STAGE_COLS.map(([stage]) => (
                  <td key={stage}>{secs(run.seconds[stage])}</td>
                ))}
                <td>{num(run.items)}</td>
                <td>{num(run.tokens?.input)}</td>
                <td>{num(run.tokens?.output)}</td>
                <td>{num(run.turns)}</td>
                <td>{run.usd === null ? '—' : run.usd.toFixed(2)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function MetricsPanel() {
  const [days, setDays] = useState<number>(14)
  const [all, setAll] = useState(false)
  const { data, error } = useFetch(() => api.ops.metrics(days, all), [days, all])
  const total = data?.runs.reduce((sum, r) => sum + (r.usd ?? 0), 0) ?? 0
  return (
    <>
      <div className="ops-row">
        <label htmlFor="ops-days">期間</label>
        <select id="ops-days" value={days} onChange={(ev) => setDays(Number(ev.target.value))}>
          {DAYS.map((d) => (
            <option key={d} value={d}>
              最近 {d} 天
            </option>
          ))}
        </select>
        <label className="ops-check">
          <input type="checkbox" checked={all} onChange={(ev) => setAll(ev.target.checked)} />
          包含測試與維運工作
        </label>
      </div>
      {error ? (
        <p className="ops-msg ops-err">{error.message}</p>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          {!data.debug && (
            <p className="ops-note ops-warn">config.json 的 debug 沒有開，不會記錄新的執行紀錄（容器裡也可以用 NEWSLETTER_DEBUG=1 打開）。</p>
          )}
          {data.runs.length === 0 ? (
            <p className="ops-note">這段期間沒有執行紀錄。</p>
          ) : (
            <>
              <p className="ops-note">
                共 {data.runs.length} 次，費用合計 {total.toFixed(2)} USD（只有透過 SDK 執行的步驟有費用：撰寫、搜尋、知識圖譜）。訂閱額度不是照這個金額扣，只是參考用量。
              </p>
              <MetricsTable runs={data.runs} showLabel={data.all} />
            </>
          )}
        </>
      )}
    </>
  )
}

export function OpsMetricsPage() {
  return (
    <LocalOnly title="執行紀錄">
      <MetricsPanel />
    </LocalOnly>
  )
}

/* ---------- /ops/config：設定（唯讀） ---------- */

function SourceTable({ source }: { source: SourceFile }) {
  if ('error' in source) {
    return (
      <section className="ops-source">
        <h3>{source.file}</h3>
        <p className="ops-msg ops-err">{source.error}</p>
      </section>
    )
  }
  const defaults = Object.entries(source.defaults)
  return (
    <section className="ops-source">
      <h3>
        {source.label || source.topic || source.file}
        <span className="ops-hint">
          {' '}
          {source.topic && `${source.topic} · `}
          {source.file} · {source.feeds.length} 個來源
        </span>
      </h3>
      {defaults.length > 0 && (
        <p className="ops-note">
          預設：
          {defaults.map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join('、')}
        </p>
      )}
      <div className="ops-table-wrap">
        <table className="ops-table ops-feeds">
          <thead>
            <tr>
              <th scope="col">名稱</th>
              <th scope="col">類型</th>
              <th scope="col">網址</th>
              <th scope="col">備註</th>
            </tr>
          </thead>
          <tbody>
            {source.feeds.map((feed, i) => (
              <tr key={`${feed.name}-${i}`}>
                <th scope="row">{feed.name}</th>
                <td>{feed.type}</td>
                <td className="ops-url">
                  {typeof feed.url === 'string' && SAFE_HREF.test(feed.url) ? (
                    <a href={feed.url} {...EXTERNAL}>
                      {feed.url}
                    </a>
                  ) : (
                    String(feed.url ?? '—')
                  )}
                </td>
                <td>{typeof feed.note === 'string' ? feed.note : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ConfigPanel() {
  const { data, error } = useFetch(api.ops.config, [])
  if (error) return <p className="ops-msg ops-err">{error.message}</p>
  if (!data) return <Loading />
  const feeds = data.sources.reduce((n, s) => n + ('feeds' in s ? s.feeds.length : 0), 0)
  return (
    <>
      <p className="ops-note">
        唯讀。設定檔在 repo 的 <code>config/</code>（有版控），跟著映像部署；要改就改 repo、合併後重新部署。執行紀錄裡「則數」很少、或「試抓來源」有失敗的來源時，來這裡對照。
      </p>
      <section className="ops-section" aria-label="新聞來源">
        <h2>
          新聞來源<span className="ops-hint">（{data.sources.length} 個主題、{feeds} 個來源）</span>
        </h2>
        {data.sources.length === 0 ? <p className="ops-note">沒有 config/sources.d/*.json。</p> : data.sources.map((s) => <SourceTable key={s.file} source={s} />)}
      </section>
      <section className="ops-section" aria-label="興趣">
        <h2>興趣（interests.md）</h2>
        {data.interests === null ? <p className="ops-note">沒有 config/interests.md。</p> : <pre className="ops-pre">{data.interests}</pre>}
      </section>
      <section className="ops-section" aria-label="主設定">
        <h2>主設定（config.json）</h2>
        {data.config === null ? (
          <p className="ops-msg ops-err">讀不到 config/config.json，或不是合法的 JSON。</p>
        ) : (
          <details>
            <summary>展開</summary>
            <pre className="ops-pre">{JSON.stringify(data.config, null, 2)}</pre>
          </details>
        )}
      </section>
    </>
  )
}

export function OpsConfigPage() {
  return (
    <LocalOnly title="設定">
      <ConfigPanel />
    </LocalOnly>
  )
}
