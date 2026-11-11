import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { edgePath, layoutEvents, RADIUS, shortDate, shortLabel } from '../kgLayout'
import type { Flags, KgEntityType, KgEvent, KgGraph, KgKind, KgLink, KgStatus } from '../types'
import { FactFlagButton } from './FactFlagButton'
import { EXTERNAL } from './Inline'

export const TYPE_LABEL: Record<KgEntityType, string> = {
  org: '組織',
  person: '人物',
  product: '產品',
  place: '地點',
  other: '其他',
}

const STATUS_LABEL: Record<KgStatus, string> = {
  active: '',
  disputed: '有爭議',
  invalid: '已失效',
}

const KIND_LABEL: Record<KgKind, string> = {
  same: '同一件事',
  follow: '後續發展',
  contradict: '說法矛盾',
  retract: '撤回／否認',
}

/** 從選中的事件看出去：它是 src（較新）時、它是 dst（較舊）時的說法 */
const OUTGOING: Record<KgKind, string> = {
  same: '同一件事',
  follow: '後續自',
  contradict: '說法矛盾於',
  retract: '撤回／否認了',
}
const INCOMING: Record<KgKind, string> = {
  same: '同一件事',
  follow: '後續',
  contradict: '被質疑',
  retract: '被撤回／否認',
}

export const entityPath = (key: string) => `/kg/entity/${encodeURIComponent(key)}`

function StatusBadge({ status }: { status: KgStatus }) {
  return status === 'active' ? null : <span className={`kg-badge kg-badge-${status}`}>{STATUS_LABEL[status]}</span>
}

function Legend() {
  return (
    <ul className="kg-legend" aria-label="圖例">
      {(['follow', 'same', 'contradict', 'retract'] as const).map((kind) => (
        <li key={kind}>
          <svg width="28" height="10" aria-hidden="true">
            <line className={`kg-edge kg-edge-${kind}`} x1="2" y1="5" x2="26" y2="5" />
          </svg>
          {KIND_LABEL[kind]}
        </li>
      ))}
      <li>
        <span className="kg-dot kg-dot-disputed" aria-hidden="true" />
        有爭議
      </li>
      <li>
        <span className="kg-dot kg-dot-invalid" aria-hidden="true" />
        已失效
      </li>
    </ul>
  )
}

/** 圖本身只給滑鼠與觸控（aria-hidden）；鍵盤與螢幕閱讀器用下面的事件列表，內容相同。 */
function Canvas({ graph, seeds, selected, onSelect }: { graph: KgGraph; seeds: Set<number>; selected: number | null; onSelect: (id: number) => void }) {
  const { width, height, columns, nodes } = useMemo(() => layoutEvents(graph.events), [graph.events])
  const ref = useRef<HTMLDivElement>(null)
  const selectedX = selected === null ? undefined : nodes.get(selected)?.x
  // 選中的事件捲進可見範圍（第一次進來時通常是最右邊、最新的那幾欄）
  useEffect(() => {
    const box = ref.current
    if (box && selectedX !== undefined && box.clientWidth) box.scrollLeft = Math.max(0, selectedX - box.clientWidth / 2)
  }, [selectedX])
  return (
    <div ref={ref} className="kg-canvas">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
        {columns.map((c) => (
          <g key={c.date}>
            <line className="kg-guide" x1={c.x} y1={26} x2={c.x} y2={height - 8} />
            <text className="kg-date" x={c.x} y={16} textAnchor="middle">
              {shortDate(c.date)}
            </text>
          </g>
        ))}
        {graph.links.map((link, i) => {
          const from = nodes.get(link.src)
          const to = nodes.get(link.dst)
          if (!from || !to) return null
          const active = selected === link.src || selected === link.dst
          return <path key={i} className={`kg-edge kg-edge-${link.kind}`} data-active={active || undefined} d={edgePath(from, to)} />
        })}
        {graph.events.map((event) => {
          const at = nodes.get(event.id)!
          return (
            <g
              key={event.id}
              className={`kg-node kg-node-${event.status}`}
              data-seed={seeds.has(event.id) || undefined}
              data-selected={event.id === selected || undefined}
              transform={`translate(${at.x},${at.y})`}
              onClick={() => onSelect(event.id)}
            >
              <title>{event.summary}</title>
              <circle r={RADIUS} />
              <text y={RADIUS + 14} textAnchor="middle">
                {shortLabel(event.summary)}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function Relations({ event, links, byId, onSelect }: { event: KgEvent; links: KgLink[]; byId: Map<number, KgEvent>; onSelect: (id: number) => void }) {
  const rows = links.flatMap((link) => {
    const outgoing = link.src === event.id
    const other = byId.get(outgoing ? link.dst : link.src)
    if (!other || (!outgoing && link.dst !== event.id)) return []
    return [{ link, other, label: (outgoing ? OUTGOING : INCOMING)[link.kind] }]
  })
  if (!rows.length) return null
  return (
    <ul className="kg-relations">
      {rows.map(({ link, other, label }, i) => (
        <li key={i} data-kind={link.kind}>
          <span className={`kg-kind kg-kind-${link.kind}`}>{label}</span>
          <button type="button" className="kg-linkish" onClick={() => onSelect(other.id)}>
            {other.date}：{other.summary}
          </button>
          {link.quote && <q className="kg-evidence">{link.quote}</q>}
        </li>
      ))}
    </ul>
  )
}

interface DetailProps {
  event: KgEvent
  graph: KgGraph
  byId: Map<number, KgEvent>
  flags: Flags
  onFlag: (uid: string, flag: 'wrong' | '') => void
  onSelect: (id: number) => void
  centered: boolean
}

function Detail({ event, graph, byId, flags, onFlag, onSelect, centered }: DetailProps) {
  return (
    <section className="kg-detail" aria-label="事件詳情" aria-live="polite">
      <p className="kg-meta">
        {event.date} · {event.source}
        {event.sources > 1 && ` 等 ${event.sources} 個來源`} <StatusBadge status={event.status} />
      </p>
      <h2>{event.summary}</h2>
      {event.quote && <blockquote className="kg-quote">{event.quote}</blockquote>}
      <p className="kg-source">
        原文：
        <a href={event.url} {...EXTERNAL}>
          {event.title || event.url}
        </a>
      </p>
      {event.entities.length > 0 && (
        <ul className="kg-chips" aria-label="提到的實體">
          {event.entities.map((entity) => (
            <li key={entity.key}>
              <Link to={entityPath(entity.key)} title={TYPE_LABEL[entity.type]}>
                {entity.name}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <Relations event={event} links={graph.links} byId={byId} onSelect={onSelect} />
      <p className="kg-actions">
        {event.report && <Link to={`/reports/${event.report}`}>收錄於 {event.report} 晨報</Link>}
        {!centered && <Link to={`/kg/item/${event.uid}`}>以這則為中心</Link>}
      </p>
      {event.report && (
        <div className="fb">
          <FactFlagButton uids={[event.uid]} flags={flags} onChange={onFlag} />
        </div>
      )}
    </section>
  )
}

interface Props {
  graph: KgGraph
  /** 圖的中心（單則脈絡時是那幾則自己的事件）；畫粗框，預設選中最新的一個 */
  seeds?: number[]
  /** 標了「事實有誤」之後：狀態要重算，由頁面重新載入 */
  onFlagged?: () => void
}

/** 脈絡圖：時間軸上的事件與它們之間的關係，加上選中事件的詳情與完整的事件列表。 */
export function KgView({ graph, seeds = [], onFlagged = () => {} }: Props) {
  const byId = useMemo(() => new Map(graph.events.map((e) => [e.id, e])), [graph.events])
  const seedSet = useMemo(() => new Set(seeds), [seeds])
  const fallback = seeds.at(-1) ?? graph.events.at(-1)?.id ?? null
  const [picked, setPicked] = useState<number | null>(null)
  const selected = picked !== null && byId.has(picked) ? picked : fallback
  const [flags, setFlags] = useState<Flags>(graph.flags)
  const [seenFlags, setSeenFlags] = useState(graph.flags)
  if (seenFlags !== graph.flags) {
    // 重新載入後以伺服器為準
    setSeenFlags(graph.flags)
    setFlags(graph.flags)
  }
  const onFlag = (uid: string, flag: 'wrong' | '') => {
    setFlags((current) => {
      const { [uid]: _, ...others } = current
      return flag ? { ...others, [uid]: flag } : others
    })
    onFlagged()
  }
  const event = selected === null ? undefined : byId.get(selected)
  // 列表新到舊：最近的事最常被找
  const listed = useMemo(() => graph.events.toReversed(), [graph.events])

  return (
    <div className="kg">
      <Legend />
      <Canvas graph={graph} seeds={seedSet} selected={selected} onSelect={setPicked} />
      {graph.truncated && <p className="kg-note">事件太多，只畫出其中一部分。</p>}
      {event && (
        <Detail event={event} graph={graph} byId={byId} flags={flags} onFlag={onFlag} onSelect={setPicked} centered={seedSet.has(event.id)} />
      )}
      <section className="kg-list">
        <h2>事件列表</h2>
        <ol>
          {listed.map((e) => (
            <li key={e.id}>
              <button type="button" className="kg-linkish" aria-current={e.id === selected || undefined} onClick={() => setPicked(e.id)}>
                <span className="kg-list-date">{e.date}</span>
                {e.summary}
              </button>
              <StatusBadge status={e.status} />
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
