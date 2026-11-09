import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { groupReport, type Unit } from '../reportUnits'
import type { Adjacent, Block, ListItem, Mark, Marks, Report } from '../types'
import { isWide, useSectionNav } from '../useSectionNav'
import { FeedbackButtons } from './FeedbackButtons'
import { Inline } from './Inline'
import { ReaderShell } from './ReaderShell'

type OnMark = (uid: string, mark: Mark) => void

interface Votes {
  marks: Marks
  onMark: OnMark
}

function Items({ items, votable, votes }: { items: ListItem[]; votable: boolean; votes: Votes }) {
  return (
    <ul>
      {items.map((item, i) => (
        <li key={i}>
          <Inline nodes={item.inline} />
          {item.children && <Items items={item.children} votable={votable} votes={votes} />}
          {votable && item.uids?.length ? <FeedbackButtons uids={item.uids} marks={votes.marks} onChange={votes.onMark} /> : null}
        </li>
      ))}
    </ul>
  )
}

function BlockView({ block, votes }: { block: Block; votes: Votes }) {
  switch (block.type) {
    case 'title':
      return (
        <div>
          <h1>{block.title}</h1>
          {block.date && <p className="report-date">{block.date.replaceAll('-', '/')}</p>}
        </div>
      )
    case 'heading':
      return (
        <h2>
          <Inline nodes={block.inline} />
        </h2>
      )
    case 'callout':
      return (
        <div className="callout">
          <Inline nodes={block.inline} />
        </div>
      )
    case 'paragraph':
      return (
        <p>
          <Inline nodes={block.inline} />
        </p>
      )
    case 'list':
      return <Items items={block.items} votable={block.votable !== false} votes={votes} />
    case 'mark':
      return block.votable !== false ? <FeedbackButtons uids={[block.uid]} marks={votes.marks} onChange={votes.onMark} /> : null
  }
}

function UnitView({ unit, sources, marks, onMark }: { unit: Unit; sources: Report['sources']; marks: Marks; onMark: OnMark }) {
  const votes: Votes = { marks, onMark }
  // 用 data-unit 而不是 id：自己捲動才能避開 sticky 標頭
  return (
    <section className="unit" data-unit={unit.id}>
      {unit.kind === 'sources' ? (
        <>
          <h2>資料來源</h2>
          <ol className="sources">
            {sources.map((source) => (
              <li key={source.url}>
                <a href={source.url}>{source.title}</a>
              </li>
            ))}
          </ol>
        </>
      ) : (
        unit.blocks.map((block, i) => <BlockView key={i} block={block} votes={votes} />)
      )}
    </section>
  )
}

/** 晨報本體。≥1100px 左側多一份目錄（scroll-spy、j／k 切換），窄螢幕只是依序排下來。 */
/** 內文最後的「前一份／下一份」；[ ／ ] 鍵也能換（輸入框裡、帶修飾鍵時不攔截）。 */
function AdjacentNav({ prev, next }: Adjacent) {
  const navigate = useNavigate()
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return
      if ((e.target as Element | null)?.closest?.('input, textarea, select, [contenteditable]')) return
      const target = e.key === '[' ? prev : e.key === ']' ? next : null
      if (!target) return
      e.preventDefault()
      navigate(`/reports/${target}`)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [prev, next, navigate])
  return (
    <nav className="report-adjacent" aria-label="前後的晨報">
      {prev && (
        <Link to={`/reports/${prev}`} rel="prev">
          ← 前一份 {prev}
        </Link>
      )}
      {next && (
        <Link to={`/reports/${next}`} rel="next">
          下一份 {next} →
        </Link>
      )}
    </nav>
  )
}

export function ReportView({ report, marks, onMark, adjacent = {} }: { report: Report; marks: Marks; onMark: OnMark; adjacent?: Adjacent }) {
  const grouped = useMemo(() => groupReport(report), [report])
  const ids = useMemo(() => grouped.units.map((u) => u.id), [grouped])
  const articleRef = useRef<HTMLElement>(null)
  const navRef = useRef<HTMLElement>(null)
  const { activeId, goTo, onLinkClick } = useSectionNav(ids, articleRef, navRef)

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || !isWide()) return
      if ((e.target as Element | null)?.closest?.('input, textarea, select, [contenteditable]')) return
      const step = e.key === 'j' ? 1 : e.key === 'k' ? -1 : 0
      const target = step ? ids[ids.indexOf(activeId) + step] : undefined
      if (!target) return
      e.preventDefault()
      goTo(target)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [ids, activeId, goTo])

  const navGroups = [
    ...grouped.groups.map((g) => ({ label: g.heading ? g.label : '', units: g.units })),
    ...(grouped.sources ? [{ label: '', units: [grouped.sources] }] : []),
  ]

  return (
    <article ref={articleRef} className="report">
      <ReaderShell
        side={
          <>
            {grouped.title && <BlockView block={grouped.title} votes={{ marks, onMark }} />}
            <nav ref={navRef} className="reader-nav" aria-label="新聞目錄">
              {navGroups.map((group, gi) => (
                <div key={gi}>
                  {group.label && <p className="reader-nav-title">{group.label}</p>}
                  <ul>
                    {group.units.map((unit) => (
                      <li key={unit.id}>
                        <a
                          href={`#${unit.id}`}
                          title={unit.label}
                          aria-current={unit.id === activeId ? 'true' : undefined}
                          onClick={onLinkClick(unit.id)}
                        >
                          <span>{unit.label}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </nav>
          </>
        }
      >
        {grouped.callout && <BlockView block={grouped.callout} votes={{ marks, onMark }} />}
        {grouped.groups.map((group, gi) => (
          <div key={gi} className="group">
            {group.heading && (
              <h2 className="group-title">
                <Inline nodes={group.heading.inline} />
              </h2>
            )}
            {group.units.map((unit) => (
              <UnitView key={unit.id} unit={unit} sources={report.sources} marks={marks} onMark={onMark} />
            ))}
          </div>
        ))}
        {grouped.sources && <UnitView unit={grouped.sources} sources={report.sources} marks={marks} onMark={onMark} />}
        {(adjacent.prev || adjacent.next) && <AdjacentNav {...adjacent} />}
      </ReaderShell>
    </article>
  )
}

/** 晨報加上標記狀態，以伺服器回傳的結果更新。 */
export function ReportPanel({ report, marks: initial, adjacent }: { report: Report; marks: Marks; adjacent?: Adjacent }) {
  const [marks, setMarks] = useState(initial)
  const onMark: OnMark = (uid, mark) => setMarks((current) => ({ ...current, [uid]: mark }))
  return <ReportView report={report} marks={marks} onMark={onMark} adjacent={adjacent} />
}
