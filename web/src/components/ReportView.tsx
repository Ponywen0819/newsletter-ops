import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { groupReport, type Unit } from '../reportUnits'
import type { Block, ListItem, Mark, Marks, Report } from '../types'
import { FeedbackButtons } from './FeedbackButtons'
import { Inline } from './Inline'

type OnMark = (uid: string, mark: Mark) => void

// 與 styles.css 的閱讀器斷點同一個值。沒有 matchMedia（jsdom）時當作寬螢幕，鍵盤切換才測得到
const isWide = () => window.matchMedia?.('(min-width: 1100px)').matches ?? true

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
      return <Items items={block.items} votable={block.votable} votes={votes} />
    case 'mark':
      return block.votable ? <FeedbackButtons uids={[block.uid]} marks={votes.marks} onChange={votes.onMark} /> : null
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
  const [activeId, setActiveId] = useState(() => {
    const fromHash = window.location.hash.slice(1)
    return ids.includes(fromHash) ? fromHash : (ids[0] ?? '')
  })
  const articleRef = useRef<HTMLElement>(null)
  const navRef = useRef<HTMLElement>(null)
  // 點目錄／按 j／k 之後的捲動動畫期間，不讓 scroll-spy 把剛選的那一則改掉
  const lockUntil = useRef(0)

  const scrollToUnit = useCallback((id: string, instant = false) => {
    const unit = articleRef.current?.querySelector<HTMLElement>(`[data-unit="${id}"]`)
    if (!unit) return
    // 該分類的第一則：捲到分類標題，才看得出它屬於哪一類
    const prev = unit.previousElementSibling
    const target = prev?.classList.contains('group-title') ? prev : unit
    setActiveId(id)
    lockUntil.current = Date.now() + 800
    // 平順與否交給 CSS 的 scroll-behavior（尊重 prefers-reduced-motion）；載入時帶 hash 直接到位
    target.scrollIntoView?.({ block: 'start', behavior: instant ? 'instant' : 'auto' })
  }, [])

  const goTo = useCallback(
    (id: string) => {
      // 沿用 history.state：React Router 把自己的 key／idx 存在裡面，換成 null 會弄壞上一頁
      window.history.replaceState(window.history.state, '', `#${id}`)
      scrollToUnit(id)
    },
    [scrollToUnit],
  )

  // 網址帶 hash 開頁：直接捲到那一則
  useEffect(() => {
    const id = window.location.hash.slice(1)
    if (ids.includes(id)) scrollToUnit(id, true)
  }, [ids, scrollToUnit])

  // 上一頁／下一頁、手改網址的 hash
  useEffect(() => {
    const onHashChange = () => {
      const id = window.location.hash.slice(1)
      if (ids.includes(id)) scrollToUnit(id)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [ids, scrollToUnit])

  // scroll-spy：頁面上方 30% 那條線以上、最後一個開頭已經過線的單位就是目前這一則；捲到底時是最後一則
  // （最後幾則很短的話，捲到底也碰不到那條線）
  useEffect(() => {
    const onScroll = () => {
      if (Date.now() < lockUntil.current || !isWide()) return
      const units = [...(articleRef.current?.querySelectorAll<HTMLElement>('[data-unit]') ?? [])]
      let id = units[0]?.dataset.unit
      for (const unit of units) {
        if (unit.getBoundingClientRect().top > window.innerHeight * 0.3) break
        id = unit.dataset.unit
      }
      if (window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) id = units.at(-1)?.dataset.unit
      if (id) setActiveId(id)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [])

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

  // 目錄比視窗長時，目前這一則要留在看得到的地方。只捲目錄自己：用 scrollIntoView 的話
  // 可能連帶影響頁面正在進行的捲動
  useEffect(() => {
    const nav = navRef.current
    const item = nav?.querySelector('[aria-current="true"]')
    if (!nav || !item) return
    const box = nav.getBoundingClientRect()
    const rect = item.getBoundingClientRect()
    if (rect.top < box.top) nav.scrollTop -= box.top - rect.top
    else if (rect.bottom > box.bottom) nav.scrollTop += rect.bottom - box.bottom
  }, [activeId])

  // 目錄：各分類一組，資料來源單獨一組放最後
  const navGroups = [
    ...grouped.groups.map((g) => ({ label: g.heading ? g.label : '', units: g.units })),
    ...(grouped.sources ? [{ label: '', units: [grouped.sources] }] : []),
  ]

  return (
    <article ref={articleRef} className="report reader">
      <div className="reader-side">
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
      </div>
      <div className="reader-body">
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
