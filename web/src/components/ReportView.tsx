import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { groupReport, type Unit } from '../reportUnits'
import type { Block, ListItem, Mark, Marks, Report } from '../types'
import { FeedbackButtons } from './FeedbackButtons'
import { Inline } from './Inline'

type OnMark = (uid: string, mark: Mark) => void

// 與 styles.css 的閱讀器斷點同一個值。沒有 matchMedia（jsdom）時當作寬螢幕，鍵盤切換才測得到
const isWide = () => window.matchMedia?.('(min-width: 1100px)').matches ?? true

function Items({ items, marks, onMark }: { items: ListItem[]; marks: Marks; onMark: OnMark }) {
  return (
    <ul>
      {items.map((item, i) => (
        <li key={i}>
          <Inline nodes={item.inline} />
          {item.children && <Items items={item.children} marks={marks} onMark={onMark} />}
          {item.uids?.map((uid) => (
            <FeedbackButtons key={uid} uid={uid} mark={marks[uid] ?? ''} onChange={onMark} />
          ))}
        </li>
      ))}
    </ul>
  )
}

function BlockView({ block, marks, onMark }: { block: Block; marks: Marks; onMark: OnMark }) {
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
      return <Items items={block.items} marks={marks} onMark={onMark} />
    case 'mark':
      return <FeedbackButtons uid={block.uid} mark={marks[block.uid] ?? ''} onChange={onMark} />
  }
}

function UnitView({ unit, active, sources, marks, onMark }: { unit: Unit; active: boolean; sources: Report['sources']; marks: Marks; onMark: OnMark }) {
  // data-unit 而不是 id：網址帶 #s-6 開頁時，瀏覽器不會自己把頁面捲到那一則
  return (
    <section className="unit" data-unit={unit.id} data-active={active || undefined}>
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
        unit.blocks.map((block, i) => <BlockView key={i} block={block} marks={marks} onMark={onMark} />)
      )}
    </section>
  )
}

/**
 * 晨報本體：內容結構沿用 email（render_email.py），每則的 mark 位置換成有用／沒用按鈕；版面由 styles.css 自適應。
 *
 * 所有「閱讀單位」（reportUnits.ts）都在 DOM 裡。<1100px 全部依序顯示（一般單欄長頁），
 * ≥1100px 由 CSS 只顯示 data-active 的那一則，搭配左側清單、上一則／下一則與鍵盤切換（筆電閱讀器）。
 * 這樣窄螢幕的畫面與行為不變，也不用 matchMedia 渲染兩份。
 */
export function ReportView({ report, marks, onMark }: { report: Report; marks: Marks; onMark: OnMark }) {
  const grouped = useMemo(() => groupReport(report), [report])
  const ids = useMemo(() => grouped.units.map((u) => u.id), [grouped])
  const [activeId, setActiveId] = useState(() => {
    const fromHash = window.location.hash.slice(1)
    return ids.includes(fromHash) ? fromHash : (ids[0] ?? '')
  })
  const navRef = useRef<HTMLElement>(null)

  const index = ids.indexOf(activeId)
  const prev = ids[index - 1]
  const next = ids[index + 1]

  const select = useCallback((id: string) => {
    setActiveId(id)
    // 沿用 history.state：React Router 把自己的 key／idx 存在裡面，換成 null 會弄壞上一頁
    window.history.replaceState(window.history.state, '', `#${id}`)
    window.scrollTo(0, 0)
  }, [])

  // 上一頁／下一頁、手改網址的 hash
  useEffect(() => {
    const onHashChange = () => {
      const id = window.location.hash.slice(1)
      if (ids.includes(id)) setActiveId(id)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [ids])

  // j／k 一律切換；↑／↓ 只在頁面已經捲到頭／尾時才切換——「其餘收錄」「資料來源」比視窗還高，不能搶走它們的捲動
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || !isWide()) return
      if ((e.target as Element | null)?.closest?.('input, textarea, select, [contenteditable]')) return
      const atTop = window.scrollY <= 0
      const atBottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 1
      const target =
        e.key === 'j' || (e.key === 'ArrowDown' && atBottom) ? next : e.key === 'k' || (e.key === 'ArrowUp' && atTop) ? prev : undefined
      if (!target) return
      e.preventDefault()
      select(target)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [prev, next, select])

  // 清單比視窗長時，目前這一則要留在看得到的地方
  useEffect(() => {
    navRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [activeId])

  // 左清單：各分類一組（開頭的頭條那組沒有標題），資料來源單獨一組放最後
  const navGroups = [
    ...grouped.groups.map((g) => ({ label: g.heading ? g.label : '', units: g.units })),
    ...(grouped.sources ? [{ label: '', units: [grouped.sources] }] : []),
  ]

  return (
    <article className="report reader">
      <div className="reader-side">
        {grouped.title && <BlockView block={grouped.title} marks={marks} onMark={onMark} />}
        <nav ref={navRef} className="reader-nav" aria-label="新聞清單">
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
                        select(unit.id)
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
      </div>
      <div className="reader-body">
        {/* 今日頭條不是閱讀單位：永遠固定在最上面，切換新聞時不會跟著消失 */}
        {grouped.callout && <BlockView block={grouped.callout} marks={marks} onMark={onMark} />}
        {grouped.groups.map((group, gi) => (
          <div key={gi} className="group">
            {group.heading && (
              <h2 className="group-title">
                <Inline nodes={group.heading.inline} />
              </h2>
            )}
            {group.units.map((unit) => (
              <UnitView key={unit.id} unit={unit} active={unit.id === activeId} sources={report.sources} marks={marks} onMark={onMark} />
            ))}
          </div>
        ))}
        {grouped.sources && <UnitView unit={grouped.sources} active={grouped.sources.id === activeId} sources={report.sources} marks={marks} onMark={onMark} />}
        <div className="reader-pager">
          <button type="button" className="pager-btn" aria-label="上一則" title="上一則（k）" disabled={!prev} onClick={() => prev && select(prev)}>
            ‹ 上一則
          </button>
          <span>
            {index + 1} / {ids.length}
          </span>
          <button type="button" className="pager-btn" aria-label="下一則" title="下一則（j）" disabled={!next} onClick={() => next && select(next)}>
            下一則 ›
          </button>
        </div>
      </div>
    </article>
  )
}

/** 晨報加上它自己的標記狀態：按了有用／沒用以伺服器回傳的結果更新畫面，不必重新載入整頁。 */
export function ReportPanel({ report, marks: initial }: { report: Report; marks: Marks }) {
  const [marks, setMarks] = useState(initial)
  const onMark: OnMark = (uid, mark) => setMarks((current) => ({ ...current, [uid]: mark }))
  return <ReportView report={report} marks={marks} onMark={onMark} />
}
