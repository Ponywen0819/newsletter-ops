import { useMemo, useRef, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { ReaderShell } from '../components/ReaderShell'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'
import { groupByMonth, type MonthGroup } from '../reportMonths'
import type { ReportSummary } from '../types'
import { useSectionNav } from '../useSectionNav'

function Rows({ items }: { items: ReportSummary[] }) {
  return (
    <ul className="reports">
      {items.length === 0 && <li>目前還沒有任何晨報。</li>}
      {items.map(({ date, headline }) => (
        <li key={date}>
          <Link to={`/reports/${date}`}>{date}</Link>
          <span>{headline}</span>
        </li>
      ))}
    </ul>
  )
}

/** 左欄的搜尋框：送出後改網址的 ?q=，可以分享、上一頁回得去；清空就回到列表。 */
function SearchBox({ q }: { q: string }) {
  const [, setParams] = useSearchParams()
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const next = String(new FormData(e.currentTarget).get('q') ?? '').trim()
    setParams(next ? { q: next } : {})
  }
  return (
    <form role="search" className="report-search" onSubmit={submit}>
      <input key={q} type="search" name="q" defaultValue={q} placeholder="搜尋歷史晨報" aria-label="搜尋歷史晨報" maxLength={100} />
    </form>
  )
}

function Side({ q, children }: { q: string; children?: ReactNode }) {
  return (
    <>
      <h1>歷史晨報</h1>
      <SearchBox q={q} />
      {children}
    </>
  )
}

/** 把 text 裡的 q（不分大小寫）包成 <mark>。 */
function highlight(text: string, q: string): ReactNode[] {
  const lower = text.toLowerCase()
  const needle = q.toLowerCase()
  const out: ReactNode[] = []
  let at = 0
  for (let i = lower.indexOf(needle); needle && i >= 0; i = lower.indexOf(needle, at)) {
    out.push(text.slice(at, i), <mark key={i}>{text.slice(i, i + q.length)}</mark>)
    at = i + q.length
  }
  out.push(text.slice(at))
  return out
}

function SearchResults({ q }: { q: string }) {
  const { data, error } = useFetch(() => api.search(q), [q, useRecovered()])
  let body: ReactNode
  if (error) body = <Notice title="搜尋失敗">{error.message}</Notice>
  else if (!data) body = <Loading />
  else
    body = (
      <>
        <p className="report-search-count" role="status">
          {data.results.length ? `找到 ${data.results.length}${data.truncated ? '+' : ''} 筆` : `沒有找到「${q}」`}
        </p>
        <ul className="reports">
          {data.results.map(({ date, text }, i) => (
            <li key={`${date}-${i}`}>
              <Link to={`/reports/${date}`}>{date}</Link>
              <span>{highlight(text, q)}</span>
            </li>
          ))}
        </ul>
        {data.truncated && <p className="report-search-count">只列出最新的 {data.results.length} 筆，請用更精確的字詞。</p>}
      </>
    )
  return <ReaderShell side={<Side q={q} />}>{body}</ReaderShell>
}

export function ReportListPage() {
  const q = useSearchParams()[0].get('q')?.trim() ?? ''
  useDocumentTitle(q ? `搜尋「${q}」— 歷史晨報` : '歷史晨報')
  return q ? <SearchResults q={q} /> : <AllReports />
}

function AllReports() {
  const { data, error } = useFetch(api.reports, [useRecovered()])

  if (error)
    return (
      <ReaderShell side={<Side q="" />}>
        <Notice title="讀取失敗">{error.message}</Notice>
      </ReaderShell>
    )
  if (!data)
    return (
      <ReaderShell side={<Side q="" />}>
        <Loading />
      </ReaderShell>
    )
  return <ReportList reports={data} />
}

function ReportList({ reports }: { reports: ReportSummary[] }) {
  const months = useMemo(() => groupByMonth(reports), [reports])
  // 只有一個月份（或沒有晨報）：分組與目錄都沒有意義，維持一張列表
  if (months.length < 2)
    return (
      <ReaderShell side={<Side q="" />}>
        <Rows items={reports} />
      </ReaderShell>
    )
  return <MonthList months={months} />
}

/** 兩個月以上：依月份分組，左欄多一份月份目錄。 */
function MonthList({ months }: { months: MonthGroup[] }) {
  const ids = useMemo(() => months.map((m) => m.id), [months])
  const rootRef = useRef<HTMLDivElement>(null)
  const navRef = useRef<HTMLElement>(null)
  const { activeId, onLinkClick } = useSectionNav(ids, rootRef, navRef)
  return (
    <ReaderShell
      ref={rootRef}
      side={
        <Side q="">
          <nav ref={navRef} className="reader-nav reader-nav-months" aria-label="月份目錄">
            <ul>
              {months.map((month) => (
                <li key={month.id}>
                  <a href={`#${month.id}`} aria-current={month.id === activeId ? 'true' : undefined} onClick={onLinkClick(month.id)}>
                    <span>{month.label}</span> <small className="reader-nav-count">{month.items.length} 份</small>
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </Side>
      }
    >
      {months.map((month) => (
        <section key={month.id} className="unit" data-unit={month.id}>
          <h2>{month.label}</h2>
          <Rows items={month.items} />
        </section>
      ))}
    </ReaderShell>
  )
}
