import { useEffect, useMemo, useRef, useState } from 'react'
import { groupReport, type Unit } from '../reportUnits'
import type { Block, ListItem, Mark, Marks, Report } from '../types'
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
  // 哪些清單可以投票（votable）由後端決定，這裡只照畫
  const votes: Votes = { marks, onMark }
  // data-unit 而不是 id：網址帶 #s-6 時由 ReportView 自己捲過去（要避開 sticky 標頭），不讓瀏覽器預設的錨點跳轉搶先
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

/**
 * 晨報本體：內容結構沿用 email（render_email.py），每則的 mark 位置換成有用／沒用按鈕；版面由 styles.css 自適應。
 *
 * 一律是一整頁往下捲的長頁（所有「閱讀單位」依序顯示，見 reportUnits.ts）。≥1100px 左側多一份「目錄」：
 * 捲到哪一則就跟著亮起，點一下捲過去，j／k 跳到下一則／上一則。這份目錄只是導覽，不改變內文怎麼顯示，
 * 所以窄螢幕的畫面與行為不變，也不用 matchMedia 渲染兩份。
 */
export function ReportView({ report, marks, onMark }: { report: Report; marks: Marks; onMark: OnMark }) {
  const grouped = useMemo(() => groupReport(report), [report])
  const ids = useMemo(() => grouped.units.map((u) => u.id), [grouped])
  const articleRef = useRef<HTMLElement>(null)
  const navRef = useRef<HTMLElement>(null)
  const { activeId, goTo } = useSectionNav(ids, articleRef, navRef)

  // j／k 跳到下一則／上一則。方向鍵不碰，留給一般捲動
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

  // 目錄：各分類一組，資料來源單獨一組放最後
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
                          onClick={(e) => {
                            if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return // 新分頁開啟照舊
                            e.preventDefault()
                            goTo(unit.id)
                          }}
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
      </ReaderShell>
    </article>
  )
}

/** 晨報加上它自己的標記狀態：按了有用／沒用以伺服器回傳的結果更新畫面，不必重新載入整頁。 */
export function ReportPanel({ report, marks: initial }: { report: Report; marks: Marks }) {
  const [marks, setMarks] = useState(initial)
  const onMark: OnMark = (uid, mark) => setMarks((current) => ({ ...current, [uid]: mark }))
  return <ReportView report={report} marks={marks} onMark={onMark} />
}
