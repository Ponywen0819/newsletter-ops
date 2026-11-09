import { api } from '../api'
import { Loading, Notice } from '../components/Notice'
import { useDocumentTitle, useFetch, useRecovered } from '../hooks'
import type { FeedbackStats, StatRow } from '../types'

const GROUPS: [keyof FeedbackStats['groups'], string][] = [
  ['source', '來源'],
  ['topic', '主題'],
  ['keyword', '命中的關鍵字'],
]
const TOP = 20

function Table({ label, rows }: { label: string; rows: StatRow[] }) {
  if (!rows.length) return null
  return (
    <section className="stats-group">
      <h2>{label}</h2>
      <table className="stats-table" aria-label={label}>
        <thead>
          <tr>
            <th scope="col">{label}</th>
            <th scope="col">有用</th>
            <th scope="col">沒用</th>
            <th scope="col">有用比例</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, TOP).map((r) => (
            <tr key={r.name}>
              <th scope="row">{r.name}</th>
              <td>{r.pos}</td>
              <td>{r.neg}</td>
              <td>{Math.round((100 * r.pos) / (r.pos + r.neg))}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > TOP && <p className="stats-note">只列則數最多的 {TOP} 個（共 {rows.length} 個）。</p>}
    </section>
  )
}

/** 回饋統計：newsletter-feedback --stats 的網頁版，給調 config 與 interests.md 時參考。 */
export function StatsPage() {
  const { data, error } = useFetch(api.feedbackStats, [useRecovered()])
  useDocumentTitle('回饋統計')
  if (error) return <Notice title="讀取失敗">{error.message}</Notice>
  if (!data) return <Loading />
  return (
    <>
      <h1>回饋統計</h1>
      {data.total ? (
        <p className="stats-note">
          共 {data.total} 則有標記（每則只算最後一次，取消的不算）。收錄很多卻少有「有用」的關鍵字可以降權，常拿到「有用」卻不在 boost 清單的可以加進去。
        </p>
      ) : (
        <p className="stats-note">還沒有任何回饋。在晨報或 email 裡按「有用／沒用」之後，這裡會列出統計。</p>
      )}
      {GROUPS.map(([key, label]) => (
        <Table key={key} label={label} rows={data.groups[key]} />
      ))}
    </>
  )
}
